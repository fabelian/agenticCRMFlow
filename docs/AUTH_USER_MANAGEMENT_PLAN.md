# 인증 · 사용자 관리 · 권한 실행 계획

> 대상 프로젝트: `agenticCRM_flow`
> 최초 작성: 2026-04-25 (KST)
> 범위: NBA 3단계 승인 워크플로우(`docs/NBA_APPROVAL_WORKFLOW_PLAN.md`)의 **선결 프로젝트**. 자체 비밀번호·세션 인증, admin 주도 사용자 관리, 4단계 역할(admin/crm/sales/viewer) 다중 보유, CLI용 API key, 점진적(`AUTH_ENFORCEMENT=off→soft→strict`) 도입을 다룬다.
> 후속 계획: 6~12개월 내 Google SSO + MFA로 마이그레이션(Phase D, 별도 문서로 분리 예정)

---

## 0. 한눈에 보는 현재 상태

### 0-1. 현재 인증·권한 상태

| 레이어 | 현재 | 필요 |
|---|---|---|
| 인증 | ❌ 전무 — 누구나 모든 엔드포인트 호출 가능 (CodeReview.md `#Fix 3`) | 세션 기반 로그인, secure cookie, CSRF 보호 |
| 사용자 모델 | ❌ 없음 | `users` 테이블 + 다중 역할 |
| 권한 검증 | ❌ 없음 | FastAPI `Depends(require_role(...))` 미들웨어 |
| 감사 로그 | ❌ 인증 이벤트 추적 0 | `auth_events` append-only 테이블 |
| CLI 인증 | ❌ `src/main.py`도 무인증 | 사용자별 API key, 헤더 인증 |
| 전역 가변 상태 | `_model_setting`, `running_set` 요청 간 공유 (CodeReview `#Fix 2`) | 요청 단위 컨텍스트 (인증 도입과 함께 자연 해소) |
| XSS 방어 | `escHtml`이 따옴표 미이스케이프 (CodeReview `#Fix 1`) | 사용자 입력(이름·메모) 렌더 전 강화 필수 |

### 0-2. 사용자 결정 (2026-04-25 확정)

| # | 항목 | 결정 |
|---|---|---|
| 1 | 인증 방식 | **A. 세션 기반 + bcrypt** (자체 비밀번호). 6~12개월 내 Google SSO로 마이그레이션 예정 |
| 2 | 초대 흐름 | **admin이 임시 토큰 URL 받아 직접 카톡/메일로 전달** — 이메일 인프라(SES/SendGrid) 도입 X |
| 3 | Rate limit | **분당 10회 시도 허용, 그 이상 연속 실패 시 5분 lockout** (계정·IP 모두) |
| 4 | CLI 인증 | **관리자 화면에서 1클릭으로 API key 발급** — 평문은 발급 시 한 번만 표시, 서버는 hash만 저장 |
| 5 | `AUTH_ENFORCEMENT=soft` 운영 | **2주** |
| 6 | viewer 역할 | **필요** — 영업팀 외 부서 읽기 전용 접근 |
| 7 | MFA | **Phase 3 SSO 마이그레이션 시점에 한꺼번에** 처리 (현 단계 미도입) |

### 0-3. 이 프로젝트만의 제약 & 자산

- **KST 일관성**: NBA 계획과 동일하게 `data_tools.now_kst_str()` 재사용. 모든 인증 타임스탬프(로그인·세션·이벤트)는 KST 분 단위.
- **Append-only 감사 패턴 선례**: NBA 계획의 `approval_events` 패턴을 그대로 차용해 `auth_events`도 동일 구조.
- **JSON 컬럼 모델 패턴**: `User`, `Session`, `AuthEvent`도 기존 6개 테이블처럼 `data JSON` 컬럼에 payload를 담는 동일 구조 유지(SQLAlchemy 모델 일관성).
- **`AUTH_ENFORCEMENT` 환경변수 패턴**: NBA 계획의 `AUTO_START_ACTIVITY_ON_SALES_APPROVE`, `APPROVAL_ALLOW_SELF_CHECK`와 같은 결의 환경변수 토글 정책.
- **SQLite/Postgres 자동 분기**(`src/db/database.py:18`): 인증 테이블도 동일 분기 방식으로 자동 동작.

---

## 1. 핵심 설계 결정

### 결정 A. 세션 기반 + bcrypt 비밀번호

- **저장**: `password_hash` = `bcrypt(password, cost=12)` (~200ms 검증 시간, 서버 부하와 brute-force 비용의 균형). argon2id 권장이나 bcrypt도 충분.
- **세션 토큰**: 256-bit random URL-safe (`secrets.token_urlsafe(32)`). 평문은 절대 DB에 저장 X — 서버 저장은 `sha256(token)`만. 인증 시마다 cookie의 평문을 sha256으로 해시하여 비교(bcrypt는 매 요청마다 너무 느려서 부적합).
- **Cookie 속성**: `HttpOnly + Secure + SameSite=Lax + Path=/`. 이름은 `crm_session`.
- **만료 정책**:
  - **절대 만료**: 8시간 (로그인 시점 기준)
  - **비활동 만료**: 30분 — 매 요청마다 `last_seen_at` 갱신
  - 탭 닫음 = 쿠키 유지(절대/비활동 중 빠른 쪽이 만료)
  - "Remember me" 미도입 (단순화 — 영업도구는 매일 로그인 가정)
- **로그아웃**: 서버측 세션 row의 `revoked_at = now_kst_str()` 즉시 설정. cookie도 클라이언트에서 만료.
- **다중 세션**: 한 사용자가 여러 디바이스 동시 로그인 허용. "다른 모든 세션 로그아웃" 옵션 제공(비밀번호 변경 시 자동 발동).

### 결정 B. 초대 토큰 흐름 — admin URL 핸드오프

이메일 인프라 없이 admin이 URL을 직접 사용자에게 전달. 흐름:

```
admin                                  서버                              user
  │                                      │                                │
  ├─ POST /api/admin/users ─────────────▶│                                │
  │   {email, name, roles}               │                                │
  │                                      ├ user row 생성                  │
  │                                      │   is_active=false              │
  │                                      │   password_hash=null           │
  │                                      ├ invite token 발급              │
  │                                      │   token=secrets.token_urlsafe  │
  │                                      │   expires_at=now+24h           │
  │◀──── { invite_url } ─────────────────┤                                │
  │                                      │                                │
  ├──── 카톡/메일로 invite_url 전달 ─────────────────────────────────────▶│
  │                                      │                                │
  │                                      │◀──── GET /invite/{token} ──────┤
  │                                      ├ token 검증 + 사용자 식별        │
  │                                      ├──── 비밀번호 설정 폼 ──────────▶│
  │                                      │                                │
  │                                      │◀──── POST /api/invite/accept ──┤
  │                                      │   {token, new_password}        │
  │                                      ├ password_hash 저장             │
  │                                      ├ is_active=true                 │
  │                                      ├ token 1회용 폐기               │
  │                                      ├ session 자동 생성              │
  │                                      ├──── 리다이렉트 to / ───────────▶│
```

- **invite token**: 256-bit URL-safe, 24시간 유효, 1회용. 만료/사용 후 폐기.
- **재발급**: admin이 사용자 상세에서 `[초대 토큰 재발급]` 클릭 → 이전 토큰 폐기 + 새 토큰 발급.
- **invite URL 표시**: admin 응답에 평문 URL 포함. 화면에 [복사] 버튼 + "카톡/메일로 직접 전달하세요" 안내. 페이지를 떠나면 다시 볼 수 없음(평문 한 번만).
- **중복 방지**: 같은 email로 활성 사용자가 이미 있으면 422.
- **비밀번호 재설정**: 동일 토큰 흐름을 재활용. admin이 `[비밀번호 재설정 토큰 발급]` 클릭 → 24시간 유효 토큰 발급 → URL을 사용자에게 전달.

### 결정 C. Rate limit — 분당 10회, 5분 lockout

- **계정 단위**: 한 `email`로 분당 10회 실패 시 → 그 계정 5분 lockout (성공해도 lockout은 유지).
- **IP 단위**: 한 IP(`X-Forwarded-For` 우선, 없으면 `client.host`)로 분당 10회 실패 시 → 그 IP 5분 lockout.
- **둘 다 따로 카운트** — IP 단위로 계정 1을 5회, 계정 2를 5회 시도(=10회) → 그 IP 자체 lockout.
- **카운터 저장**: 메모리 내 LRU(개발/단일 인스턴스)에서 시작. Phase 3 SSO 마이그레이션 시점에 Redis로 이전(현재는 단일 인스턴스 가정으로 충분).
- **응답**: lockout 시 `429 Too Many Requests` + `Retry-After: 300`. 응답 본문은 모호하게 — "잠시 후 다시 시도해주세요". 계정 존재 여부 누설 X.
- **성공 시 리셋**: 정상 로그인 성공하면 해당 계정·IP 카운터 리셋.
- **admin 알림**: 같은 계정에 대해 5분 lockout이 1시간 내 3회 발생 → admin 화면에 경고 배너 + `auth_events`에 `account_repeated_lockout` 기록.

라이브러리: `slowapi` 사용. FastAPI Depends에 통합.

### 결정 D. CLI용 API key — admin 1클릭 발급

- **발급**: admin이 사용자 상세 화면에서 `[API Key 발급]` 클릭. 서버가 `crm_<32-byte base64>` 형식 토큰 생성, hash(`sha256`)만 저장. 응답에 평문을 한 번만 표시.
- **사용**: HTTP 헤더 `Authorization: Bearer crm_xxx`. 세션 cookie 대신 사용 가능.
- **권한**: 발급된 사용자의 `roles`를 그대로 상속. admin 사용자가 자기 키 발급하면 admin 권한, viewer면 viewer 권한.
- **폐기**: admin이 사용자 상세에서 `[API Key 폐기]` 클릭. `revoked_at` 설정 → 즉시 무효화.
- **다중 키**: 한 사용자가 여러 키 보유 가능 (개발 환경 vs CI 분리 등). 각 키에 `name` 라벨.
- **만료**: 기본 무기한. 명시적 폐기 시점까지 유효. `expires_at` 옵션 필드(미사용).
- **CLI 사용**: `src/main.py`가 환경변수 `CRM_API_KEY` 읽어서 자동 부착. 없으면 (현재처럼) 무인증 호출 + soft 모드면 경고만, strict 모드면 401.
- **rotation**: 발급된 키를 직접 회전하는 흐름은 단순화 — 새 키 발급 → CLI 환경변수 교체 → 기존 키 폐기. 자동 rotation 미도입.

### 결정 E. `AUTH_ENFORCEMENT` 점진 강제 — 2주 soft 운영

환경변수로 인증 강제 정도를 단계적으로 조절:

| 값 | 동작 | 용도 |
|---|---|---|
| `off` | 모든 라우트 인증 검증 무시. `current_user = None` 가능. | 개발/마이그레이션 초기 |
| `soft` | 검증 시도. 실패해도 통과(401/403 안 냄)지만 `auth_events`에 `auth_violation_observed` 기록 + 응답 헤더 `X-Auth-Warning: ...`. | 2주 운영 — 누가 인증 누락된 호출 하는지 모니터링 |
| `strict` | 검증 실패 시 401/403 즉시 반환. 정식 운영 모드. | 정식 |

**전환 일정**:

1. Phase A 코드 배포 + DB 마이그레이션 → `AUTH_ENFORCEMENT=off`
2. admin 부트스트랩 + Phase B UI → admin이 사용자 추가
3. Phase C 코드 배포 → `AUTH_ENFORCEMENT=soft` 토글, 2주 모니터링
4. 모든 사용자가 정상 로그인 흐름 사용 확인 + `auth_events`의 violation 0 확인 → `strict`로 토글
5. NBA 워크플로우 Phase -1 작업 시작 (이 시점에 인증이 신뢰 가능 — Maker-Checker 검증의 의미가 비로소 생김)

`strict` 도달 후 `off`/`soft`로 되돌리는 일은 비상 상황 외 금지. 되돌릴 때마다 `auth_events`에 `enforcement_downgrade` 기록.

### 결정 F. 4단계 역할 + 다중 보유

```
admin     : 사용자 관리, 모든 데이터 mutation, AUTH_ENFORCEMENT/APPROVAL_ALLOW_SELF_CHECK 토글, 모든 감사로그 열람
sales     : viewer + sales-acknowledge/approve/revoke + 영업노트 CRUD
crm       : viewer + crm-acknowledge/approve/revoke + NBA/Activity/QC 실행
viewer    : 모든 GET (고객·노트·NBA·Activity·QC 조회), mutation 0
```

- **다중 보유**: `roles: ["admin", "crm", "sales"]` (예: 1인 운영 모드의 박성우)
- **검증 규칙**: 라우트가 `require_role("crm")`이면 사용자의 `roles` 배열에 `crm`이 포함되어 있으면 통과(다른 역할도 같이 보유 OK).
- **admin은 만능 아님**: admin도 NBA `crm-approve`를 하려면 `crm` 역할이 별도로 있어야 함. 즉 admin은 "사용자·시스템 관리 권한"이지 "모든 도메인 권한"이 아니다. 단, 모든 데이터 GET은 admin에게 허용(감사 목적).
- **role 변경**: admin만 가능. 변경 시 `auth_events.role_changed` 기록.
- **자기 자신의 admin 박탈 방지**: admin이 본인의 `admin` 역할을 제거하려는 시도 → 422 ("마지막 admin은 자기 자신을 박탈할 수 없습니다"). 시스템에 admin이 0명이 되는 것을 방지.

라우트 → 필요 role 매핑 (대표):

| 라우트 | 필요 role |
|---|---|
| `GET /api/customers`, `GET /api/customer/{id}` | viewer+ |
| `POST /api/customers`, `DELETE /api/customers/{id}` | admin |
| `GET /api/sales-notes` | viewer+ |
| `POST/PUT/DELETE /api/sales-notes` | crm or sales (영업담당자 모두 입력 가능) |
| `GET /api/run/persona|nba|activity|qc/{id}` (분석 SSE) | crm or admin |
| `GET /api/run/*-all` | admin (일괄 실행은 영향 큼) |
| `POST /api/nba/{cid}/.../crm-approve\|revoke` | crm |
| `POST /api/nba/{cid}/.../sales-approve\|revoke` | sales (+ Maker-Checker self-check) |
| `POST /api/nba/{cid}/summary/crm-acknowledge` | crm |
| `POST /api/nba/{cid}/summary/sales-acknowledge` | sales |
| `POST /api/approvals/bulk` | crm or sales (action에 따라 분기) |
| `POST /api/sales-notes/check-dislikes` | crm or sales |
| `POST /api/chat` | viewer+ |
| `GET|POST /api/admin/users` | admin |
| `GET|POST /api/admin/api-keys` | admin |
| `POST /api/me/change-password` | any logged in |

### 결정 G. MFA 보류 — Google SSO 마이그레이션 시 한꺼번에

현 단계에서 MFA(TOTP/SMS)를 자체 구현하지 않는다. 이유:
- 자체 구현은 백업 코드·복구 흐름·단말 변경 처리 등 운영 복잡도 큰 데, 작은 조직에서 ROI 낮음.
- Google Workspace SSO로 마이그레이션하면 IdP가 MFA를 자동 강제. 한 번에 깔끔히 도입.
- 따라서 Phase A~C 동안에는 비밀번호 강도 정책으로 buffer.
  - **최소 12자**
  - **공통 비밀번호 차단**: 상위 1만 빈출 사전(`common-passwords` 패키지)에 매칭되면 거부
  - **이전 비밀번호 재사용 금지**: 마지막 3개 hash 보관, 일치 시 거부
  - **비밀번호 변경 강제**: 90일 경과 시 다음 로그인에서 변경 강제 페이지로 리다이렉트(soft 알림 → 7일 후 강제)

Phase 3 SSO 마이그레이션 시점에 자체 비밀번호는 비활성화하고 IdP에 위임. MFA는 IdP 정책으로 처리.

---

## 2. DB 스키마

기존 `src/db/database.py`에 3개 모델 추가. 모두 JSON `data` 컬럼 패턴 유지(현 6개 테이블과 일관).

### 2-1. `users` 테이블

```python
class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)        # 영문 슬러그 — sungwoo, yj_kim, hr_lee
    data = Column(JSON, nullable=False)
    # data 구조:
    # {
    #   "email": "sungwoo@company.com",
    #   "name": "박성우",
    #   "password_hash": "$2b$12$...",          # bcrypt
    #   "previous_password_hashes": ["$2b$12$...", ...],  # 마지막 3개
    #   "password_changed_at": "2026-04-25 09:12",
    #   "roles": ["admin", "crm", "sales"],
    #   "is_active": true,
    #   "must_change_password": false,
    #   "created_at": "2026-04-25 09:12",
    #   "created_by": "sungwoo",
    #   "last_login_at": null,
    #   "last_login_ip": null,
    #   "deactivated_at": null,
    #   "deactivated_by": null
    # }
```

PK가 영문 슬러그 ID인 이유: 감사 로그(`actor` 필드)·NBA 승인 이력(`crm_approved_by`)에 사람이 읽기 쉬운 식별자 박기 위함. 이메일은 변경 가능하므로 PK 부적합.

### 2-2. `sessions` 테이블

```python
class Session(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True)        # token_hash (sha256)
    data = Column(JSON, nullable=False)
    # {
    #   "user_id": "sungwoo",
    #   "created_at": "2026-04-25 09:12",
    #   "last_seen_at": "2026-04-25 09:35",
    #   "expires_at": "2026-04-25 17:12",         # 절대 만료 (created + 8h)
    #   "ip": "203.0.113.45",
    #   "user_agent": "Mozilla/5.0 ...",
    #   "revoked_at": null,
    #   "revoked_by": null,                       # 자기/admin/password_changed
    #   "revoke_reason": null
    # }
```

**평문 토큰 절대 저장 X** — `id`는 `sha256(plaintext_token).hexdigest()`. 클라이언트 cookie의 평문을 매 요청마다 sha256해서 PK 조회.

비활동 만료 검증: 매 요청 시 `now_kst_str() - last_seen_at > 30분` 이면 만료. 통과하면 `last_seen_at` 갱신.

### 2-3. `api_keys` 테이블

```python
class ApiKey(Base):
    __tablename__ = "api_keys"
    id = Column(String, primary_key=True)        # key_hash (sha256)
    data = Column(JSON, nullable=False)
    # {
    #   "user_id": "sungwoo",
    #   "name": "CLI - production",
    #   "prefix": "crm_aBc12...",                 # 평문 앞 8자만 (UI 식별용)
    #   "created_at": "2026-04-25 09:12",
    #   "created_by": "sungwoo",                  # 발급한 admin
    #   "last_used_at": null,
    #   "last_used_ip": null,
    #   "expires_at": null,                       # 무기한
    #   "revoked_at": null,
    #   "revoked_by": null
    # }
```

`prefix` 8자만 저장하는 이유: admin 화면에서 "어느 키였는지" 식별 가능하면서 평문 노출 0.

### 2-4. `auth_events` 테이블 (append-only)

```python
class AuthEvent(Base):
    __tablename__ = "auth_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    data = Column(JSON, nullable=False)
    # {
    #   "event_type": "login_success",
    #   "user_id": "sungwoo",
    #   "actor": "sungwoo",                       # 행위자 (role_changed 시 변경자)
    #   "ip": "203.0.113.45",
    #   "user_agent": "...",
    #   "session_id": "abc...",                   # 해당 시 세션 hash 일부
    #   "created_at": "2026-04-25 09:12",
    #   "note": null,
    #   "extra": {}                                # event_type별 추가 정보
    # }
```

**이벤트 타입 enum**:
- `login_success` / `login_failed`
- `logout` / `logout_all_sessions`
- `session_revoked` (admin이 강제 / 비밀번호 변경 등)
- `password_changed` / `password_reset_initiated` / `password_reset_completed`
- `invite_sent` / `invite_accepted` / `invite_revoked` / `invite_expired`
- `user_created` / `user_deactivated` / `user_reactivated`
- `role_changed` (extra: `{from: [...], to: [...]}`)
- `api_key_created` / `api_key_revoked` / `api_key_used_first_time`
- `account_locked` (extra: `{lockout_until, reason: "rate_limit"}`)
- `account_repeated_lockout` (1시간 내 3회 lockout)
- `auth_violation_observed` (soft 모드, 인증 누락된 보호 라우트 호출)
- `enforcement_downgrade` (strict → soft/off 전환)

---

## 3. API 엔드포인트

### 3-1. 인증 (`web/auth_routes.py` 신규)

| Method | Path | 설명 | 인증 필요 |
|---|---|---|---|
| POST | `/api/auth/login` | `{email, password}` → cookie 설정 + `{user, csrf_token}` | 아니오 |
| POST | `/api/auth/logout` | 현재 세션 종료 | 예 |
| POST | `/api/auth/logout-all` | 본인 모든 세션 종료 | 예 |
| GET | `/api/auth/me` | 현재 사용자 + roles | 예 |
| POST | `/api/auth/change-password` | `{current_password, new_password}` → 검증 + 다른 모든 세션 자동 종료 | 예 |
| GET | `/invite/{token}` | 비밀번호 설정 폼 (HTML) | 토큰만 |
| POST | `/api/invite/accept` | `{token, new_password}` → 사용자 활성화 + 자동 로그인 | 토큰만 |

**로그인 응답 예시**:
```json
{
  "user": {
    "id": "sungwoo",
    "name": "박성우",
    "email": "sungwoo@company.com",
    "roles": ["admin", "crm", "sales"]
  },
  "csrf_token": "..."
}
```

CSRF 토큰: 로그인 응답 본문에도 포함하고, 동시에 별도 cookie `crm_csrf`(HttpOnly **X**, JS에서 읽기 가능)로도 설정. POST/PUT/DELETE 요청 시 클라이언트는 `X-CSRF-Token` 헤더에 cookie 값을 복사해 넣음(double-submit pattern).

### 3-2. 사용자 관리 (admin 전용)

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/admin/users` | 사용자 목록 (페이징·필터: role, is_active) |
| GET | `/api/admin/users/{id}` | 사용자 상세 + 활성 세션 + API key 목록 + 최근 auth_events |
| POST | `/api/admin/users` | `{id, email, name, roles}` → 사용자 생성 + 초대 토큰 발급 → invite_url 응답 |
| PATCH | `/api/admin/users/{id}` | 이름/이메일/roles 변경 |
| POST | `/api/admin/users/{id}/deactivate` | `is_active=false`, 모든 세션·API key 즉시 폐기 |
| POST | `/api/admin/users/{id}/reactivate` | 재활성화 (비밀번호 초기화 필요 — 재초대 토큰 발급) |
| POST | `/api/admin/users/{id}/reset-password` | 비밀번호 재설정 토큰 발급 → reset_url 응답 |
| POST | `/api/admin/users/{id}/revoke-all-sessions` | 강제 로그아웃 |
| POST | `/api/admin/users/{id}/api-keys` | `{name}` → API key 발급, 평문 1회 응답 |
| DELETE | `/api/admin/api-keys/{key_id}` | API key 폐기 |
| GET | `/api/admin/auth-events` | 감사 로그 조회 (필터: user_id, event_type, date_range, limit/offset) |

### 3-3. NBA 워크플로우 라우트의 인증 통합

NBA 계획서 §5의 모든 mutation 엔드포인트가 다음 패턴으로 변경:

```python
# Before
@app.post("/api/nba/{cid}/{action_id}/crm-approve")
def crm_approve(cid: str, action_id: str, body: ApproveBody, x_actor: str = Header(...)):
    ...

# After
@app.post("/api/nba/{cid}/{action_id}/crm-approve")
def crm_approve(
    cid: str,
    action_id: str,
    body: ApproveBody,
    user: User = Depends(require_role("crm")),
    _csrf: None = Depends(verify_csrf),
):
    actor = user.id
    ...
```

`X-Actor` 헤더 완전 제거. `actor`는 항상 `current_user.id`에서 도출.

---

## 4. 인증 미들웨어 (`src/auth/`)

신규 모듈 구조:

```
src/auth/
  __init__.py
  passwords.py       # bcrypt 검증, 강도 검증, 사전 차단
  sessions.py        # 세션 생성·검증·만료
  api_keys.py        # API key 발급·검증
  middleware.py      # current_user / require_role / verify_csrf Depends
  rate_limit.py      # slowapi 통합
  invite.py          # 초대/재설정 토큰
  audit.py           # auth_events 기록 헬퍼
```

### 4-1. `current_user` Depends

```python
async def current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    enforcement = os.getenv("AUTH_ENFORCEMENT", "off").lower()

    # 1) Authorization: Bearer crm_xxx → API key 경로
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user = verify_api_key(token, db)  # sha256 해시 조회
        if user:
            await audit_api_key_use(...)
            return user

    # 2) Cookie crm_session → 세션 경로
    session_token = request.cookies.get("crm_session")
    if session_token:
        user = verify_session(session_token, db, request)
        if user:
            return user

    # 3) 미인증
    if enforcement == "strict":
        raise HTTPException(401, "인증이 필요합니다")
    if enforcement == "soft":
        await audit_violation(request, "missing_auth")
        request.state.auth_warning = "missing_auth"
    return None  # off 또는 soft 통과
```

### 4-2. `require_role` Depends 팩토리

```python
def require_role(*needed: str):
    async def _dep(
        request: Request,
        user: User | None = Depends(current_user),
    ) -> User:
        enforcement = os.getenv("AUTH_ENFORCEMENT", "off").lower()
        if user is None:
            if enforcement == "strict":
                raise HTTPException(401)
            return _SYSTEM_USER if enforcement != "off" else None
        if not (set(needed) & set(user.roles)):
            if enforcement == "strict":
                raise HTTPException(403, f"필요 역할: {needed}")
            await audit_violation(request, "role_missing", needed=needed)
        return user
    return _dep
```

### 4-3. `verify_csrf` Depends

```python
async def verify_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # API key 경로(헤더 인증)는 CSRF 면제 — 브라우저 cookie 자동 첨부 위협 없음
    if request.headers.get("authorization", "").startswith("Bearer "):
        return
    cookie = request.cookies.get("crm_csrf")
    header = request.headers.get("x-csrf-token")
    if not cookie or cookie != header:
        if os.getenv("AUTH_ENFORCEMENT") == "strict":
            raise HTTPException(403, "CSRF 검증 실패")
```

### 4-4. Rate limit 통합

`slowapi`로 `/api/auth/login`, `/api/invite/accept`, `/api/auth/change-password`에만 적용:

```python
@limiter.limit("10/minute", key_func=lambda req: get_email_from_body(req))
@limiter.limit("10/minute", key_func=get_remote_address)
@app.post("/api/auth/login")
async def login(...):
    ...
```

연속 실패 시 `account_locked` 이벤트 기록 + 5분 lockout 적용. 성공 시 카운터 리셋.

---

## 5. 보안 정책 — 위협 모델

| 위협 | 완화 |
|---|---|
| **무차별 대입** (`/login` 폭격) | 결정 C — 분당 10회, 5분 lockout (계정·IP 양쪽). slowapi. |
| **세션 도용 (XSS)** | `HttpOnly + Secure + SameSite=Lax` cookie. CodeReview `#Fix 1`(escHtml) 동시 처리 — 사용자 이름·메모 등 모든 사용자 입력 렌더 시 따옴표 포함 완전 이스케이프 강제. |
| **CSRF** | SameSite=Lax + double-submit CSRF 토큰. POST/PUT/DELETE는 `X-CSRF-Token` 헤더 검증. API key 경로는 면제. |
| **비밀번호 평문 유출** | bcrypt cost 12. 로그·예외 메시지에 입력 비밀번호 출력 절대 금지. 입력 검증 실패 메시지도 일반화("이메일 또는 비밀번호가 잘못되었습니다") — 계정 존재 여부 누설 X. |
| **권한 상승 (forced browsing)** | 모든 보호 라우트는 서버측 `require_role` 강제. 클라이언트의 `currentUser.roles`는 UI 표시용일 뿐, 서버 결정에는 영향 X. |
| **세션 fixation** | 로그인 성공 시 새 세션 토큰 발급 (이전 세션 쿠키가 있더라도 폐기). |
| **타이밍 공격 (사용자 존재 여부 추론)** | 사용자 미존재 시에도 더미 bcrypt 검증을 수행해 응답 시간 일정화. lockout 카운터는 IP에는 누적, 계정에는 신중. |
| **초대 토큰 탈취** | URL이 카톡/메일로 전달되므로 평문 노출 가능성. 24시간 짧은 만료 + 1회용. 사용 후 즉시 폐기. 토큰 길이 256-bit로 추측 불가. |
| **API key 유출** | 평문은 발급 시 1회만 표시. 서버는 sha256 hash만 저장. UI에서는 prefix 8자로 식별. 의심 시 admin이 즉시 폐기 가능. |
| **세션 영구 보존** | 절대 만료 8시간 + 비활동 만료 30분 이중 정책. "Remember me" 미도입. |
| **로그 인젝션** | 사용자 입력은 `auth_events.note`에 평문 박지 말고 `extra` JSON 필드로 구조화 저장. |
| **약한 비밀번호** | 최소 12자, 상위 1만 빈출 사전 차단, 마지막 3개 hash 재사용 금지, 90일 경과 시 변경 권장(7일 후 강제). |
| **Insecure deserialization** | JSON 파싱은 stdlib만, `eval` 금지(현재 코드도 사용 안 하지만 정책 명시). |

추가로 CodeReview.md `#Fix 4`(`/api/debug`의 f-string SQL)는 인증 작업과 무관하지만 같은 시기 함께 수정. `#Fix 5`(seed 함수 이름 불일치)도 같이.

---

## 6. 프론트엔드 변경

### 6-1. 신규 페이지

- `/login` — 이메일·비밀번호 폼. 실패 시 일반화된 메시지. lockout 시 "잠시 후 다시 시도해주세요".
- `/invite/{token}` — 비밀번호 설정 폼. 강도 검증 실시간 표시(zxcvbn 점수 시각화).
- `/me` — 본인 프로필. 비밀번호 변경, 활성 세션 목록, "다른 모든 세션 로그아웃".
- `/admin/users` — 관리자 화면. 목록/상세/추가/role 변경/비활성화/세션 강제 종료/API key 발급·폐기/auth_events 조회.

### 6-2. 기존 페이지 변경

- `web/templates/index.html` (대시보드)와 `customer.html` (고객상세):
  - 헤더 우상단에 현재 사용자 이름·역할 뱃지 + 로그아웃 버튼 추가
  - 페이지 진입 시 `/api/auth/me` 호출 → 미인증이면 `/login`으로 리다이렉트(strict 모드일 때)
  - `window.currentUser = {id, name, roles}` 전역 변수 설정
  - 모든 mutation 호출에 `X-CSRF-Token` 헤더 자동 첨부 (fetch wrapper 신설)
  - role 기반 UI 토글 (NBA 계획 §7-1의 승인 버튼은 `currentUser.roles.includes("crm")`일 때만 활성)

### 6-3. `escHtml` 강화

CodeReview `#Fix 1` 해소: `index.html:3497`과 `customer.html:408` 두 구현을 통합하고 `& < > " '` 모두 이스케이프. 사용자 이름·메모·근거는 모두 이걸로 통과시킴.

```javascript
function escHtml(s) {
  if (s == null) return '';
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
```

기존의 `onclick="foo('${escHtml(x)}')"` 인라인 패턴은 점진적으로 `data-*` + 이벤트 위임으로 교체 권고(이번 Phase에서 새 코드만 강제, 기존은 베스트 에포트).

---

## 7. CLI 변경 (`src/main.py`)

```python
import os
import requests

API_BASE = os.getenv("CRM_API_BASE", "http://localhost:8000")
API_KEY = os.getenv("CRM_API_KEY")

def _headers():
    if API_KEY:
        return {"Authorization": f"Bearer {API_KEY}"}
    return {}

# 기존 직접 import 호출을 HTTP 호출로 전환할지, 또는 직접 import + 사용자 컨텍스트 주입할지 결정 필요
# 권고: CLI는 직접 import + 명시적 actor 주입 (자기 자신을 인증하는 셈이므로)
# 환경변수 CRM_CLI_USER (admin 사용자 슬러그) 필수
```

CLI 옵션 2가지:

**옵션 (1) — HTTP 호출로 전환**: CLI도 웹과 동일하게 API key로 인증된 HTTP 호출. 정합성 최고. 대신 SSE 스트리밍을 CLI에서 핸들링해야 함.

**옵션 (2) — 직접 import + `CRM_CLI_USER` 환경변수 강제**: 기존 구조 유지. 환경변수로 admin 사용자 슬러그 지정 → 모든 mutation의 `actor`로 사용. `auth_events`에 `cli_invocation` 이벤트 기록. 단순하지만 환경변수 위변조 시 위변조한 사람 정체 불명확.

**권고**: Phase 1은 (2). Phase 2에서 (1)로 마이그레이션. CLI는 사람보다 자동화 시나리오가 많으므로 (1)이 본질적으로 옳지만 우선순위 낮음.

---

## 8. NBA 워크플로우와의 인터페이스

기존 NBA 계획서(`docs/NBA_APPROVAL_WORKFLOW_PLAN.md`)의 다음 항목들이 본 계획으로 흡수·구체화된다:

| NBA 계획 항목 | 본 계획에서의 처리 |
|---|---|
| Phase -1 "인증/인가 최소 구현" | 본 계획 Phase A+B+C 전체로 대체 — `X-Actor` 헤더 방식 폐기 |
| Phase -1 "글로벌 `_model_setting`/`running_set` 제거" | 본 계획 Phase A에서 `Depends(current_user)` 도입과 함께 요청 컨텍스트로 전환 (CodeReview `#Fix 2` 동시 해소) |
| Phase -1 "`escHtml` 완전 이스케이프" | 본 계획 §6-3에서 동시 처리 (CodeReview `#Fix 1` 해소) |
| 결정 C "X-Actor 헤더" | 본 계획 결정 A·F로 대체 — `Depends(current_user)`로 항상 사용자 객체 주입 |
| 결정 C-1 "Maker-Checker (segregation_of_duties)" | 본 계획 결정 F의 `roles` 다중 보유 + NBA 측 `crm_approved_by != actor` 검증 — 양쪽이 같이 동작해야 의미. **본 계획 strict 진입 후에야 NBA Phase 0 시작 가능** |
| `APPROVAL_ALLOW_SELF_CHECK=true` 우회 플래그 | admin만 환경변수 토글. 우회 시 `auth_events.self_check_bypass` 기록 (NBA 계획 결정 C-1과 일관) |
| `nba_summary_approval` 단계 (결정 H) | 본 계획 라우트 매핑 §1 결정 F에 매핑 추가 |
| Phase 4 "ChatAgent에 `get_pending_approvals(role)` tool" | 본 계획 §3-3 `current_user` 도입으로 자동 가능 (현재 사용자 role을 자동 파라미터로) |

**경계 명시**: 본 계획이 `strict` 모드 안정화(Phase C 종료) 후에야 NBA 계획의 Phase 0 작업이 시작된다. NBA 계획의 Phase -1은 본 계획으로 완전히 대체되며, 별도로 진행하지 않는다.

NBA 계획서의 §13 개정 이력에 v3 항목 추가 필요(별도 편집).

---

## 9. 마이그레이션 & 점진 도입

### 9-1. DB 마이그레이션

`scripts/migrate_auth_schema.py`:

1. `users`, `sessions`, `api_keys`, `auth_events` 테이블 신규 생성 (`Base.metadata.create_all` 멱등).
2. 환경변수 `BOOTSTRAP_ADMIN_ID`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_NAME`, `BOOTSTRAP_ADMIN_PASSWORD` 검증.
3. 부트스트랩 admin 사용자 1명 생성 (`roles=["admin", "crm", "sales"]`, `must_change_password=true`).
4. `auth_events`에 `user_created` (actor=`system`, note=`bootstrap`) 기록.
5. 스크립트는 `users` 테이블이 비어있을 때만 실행. 재실행 시 no-op.

### 9-2. 점진 도입 일정 (Phase 단위)

```
Day 0    : DB 마이그레이션 + Phase A 코드 배포 (AUTH_ENFORCEMENT=off)
Day 0    : 부트스트랩 admin 시드
Day 1-7  : Phase B UI 배포 — admin이 모든 사용자 추가, 각자 로그인 테스트
Day 7    : Phase C 코드 배포 — 모든 라우트 require_role 부착
Day 7    : AUTH_ENFORCEMENT=soft 토글 시작
Day 7-21 : 2주 soft 운영. auth_events.auth_violation_observed / role_missing 모니터링.
           매일 admin 화면에서 violation 0인지 확인.
Day 21   : violation 0 + 모든 사용자 로그인 안정 확인 → AUTH_ENFORCEMENT=strict
Day 21+  : NBA 계획 Phase 0 작업 시작 가능
```

soft 모드에서 violation이 발견되면 해당 호출자를 식별 → 그 사용자에게 직접 안내 또는 코드 수정 → 0이 될 때까지 strict 전환 보류.

### 9-3. 롤백 전략

`AUTH_ENFORCEMENT=off`로 즉시 회귀 가능. DB 변경은 추가만 — 기존 테이블 컬럼 수정 X. 롤백 시 데이터 손실 0.

코드 롤백: 인증 미들웨어를 무력화하면 NBA 워크플로우는 X-Actor 시절로 되돌아가지 않으므로 NBA 작업 시작 전에 strict 안정화가 보장되어야 함.

---

## 10. 실행 로드맵

### Phase A. 인증 인프라 (예상 1.5~2주)

- [ ] A.1. `src/db/database.py`에 `User`, `Session`, `ApiKey`, `AuthEvent` 모델 추가
- [ ] A.2. `scripts/migrate_auth_schema.py` + 부트스트랩 admin 시드
- [ ] A.3. `src/auth/passwords.py` (bcrypt, 강도, 사전 차단, 재사용 금지)
- [ ] A.4. `src/auth/sessions.py` (생성·검증·만료·sha256 해시)
- [ ] A.5. `src/auth/api_keys.py` (발급·검증·sha256 해시)
- [ ] A.6. `src/auth/invite.py` (초대 토큰 + 재설정 토큰)
- [ ] A.7. `src/auth/audit.py` (auth_events 기록 헬퍼)
- [ ] A.8. `src/auth/middleware.py` (`current_user` / `require_role` / `verify_csrf`)
- [ ] A.9. `src/auth/rate_limit.py` (slowapi 통합, 분당 10/5분 lockout)
- [ ] A.10. `web/auth_routes.py` 신규 라우터 (`/api/auth/*`, `/invite/*`)
- [ ] A.11. `AUTH_ENFORCEMENT` 환경변수 처리
- [ ] A.12. **유닛 테스트** (`tests/test_auth_passwords.py`, `tests/test_auth_sessions.py`, `tests/test_auth_api.py`):
  - 비밀번호 강도·재사용·만료 정책
  - 세션 생성·만료·revoke·다중 세션
  - 로그인 성공·실패·rate limit·lockout
  - 초대 토큰 1회용·만료
  - CSRF 검증
  - API key 발급·사용·폐기
- [ ] A.13. CodeReview.md `#Fix 4` (f-string SQL) 동시 처리

### Phase B. 사용자·권한 관리 UI (1~1.5주)

- [ ] B.1. `/login`, `/logout` 페이지 (`web/templates/login.html`)
- [ ] B.2. `/invite/{token}` 비밀번호 설정 페이지 (zxcvbn 강도 표시)
- [ ] B.3. `/me` 본인 프로필 (비밀번호 변경, 세션 목록, 다른 세션 로그아웃)
- [ ] B.4. `/admin/users` 사용자 관리 (admin)
  - 목록·상세·추가(invite_url 1회 표시)·role 변경·비활성화
  - API key 발급(평문 1회 표시)·폐기
  - 강제 로그아웃
- [ ] B.5. `/admin/audit` 감사 로그 조회 페이지
- [ ] B.6. 헤더에 현재 사용자 + 로그아웃 버튼 (`index.html`, `customer.html`)
- [ ] B.7. fetch wrapper 신설 — CSRF 토큰 자동 첨부, 401 시 `/login` 리다이렉트
- [ ] B.8. `escHtml` 강화 (CodeReview `#Fix 1` 해소) — 모든 렌더링 경로 점검
- [ ] B.9. **통합 테스트**: 사용자 추가 → invite_url 사용 → 첫 로그인 → 비밀번호 변경 → 다른 사용자 추가 흐름

### Phase C. 기존 엔드포인트 인증 적용 (0.5~1주 + soft 2주 모니터링)

- [ ] C.1. 모든 `/api/*` 라우트에 `Depends(current_user)` 또는 `Depends(require_role(...))` 부착 — 결정 F의 매핑 표 기준
- [ ] C.2. CSRF 검증 부착 (`Depends(verify_csrf)`)
- [ ] C.3. ChatAgent (`/api/chat`) 인증 + 사용자별 대화 격리(필요 시 user_id 컨텍스트로 주입)
- [ ] C.4. CLI(`src/main.py`)의 `CRM_CLI_USER` 환경변수 처리 + auth_events `cli_invocation` 기록
- [ ] C.5. `_model_setting` 글로벌 → 요청별 컨텍스트로 (CodeReview `#Fix 2` 해소)
- [ ] C.6. `running_set`도 동일하게 (또는 락 도입 — 별도 결정)
- [ ] C.7. CodeReview.md `#Fix 5` (seed 함수 이름) 동시 처리
- [ ] C.8. AUTH_ENFORCEMENT=soft 전환 + 2주 모니터링
- [ ] C.9. violation 0 확인 → strict 전환
- [ ] C.10. **통합 테스트**: 비로그인 호출 → 401, viewer로 mutation → 403, crm으로 sales-approve → 403, 등 회귀 매트릭스 전부

### Phase D~ (NBA 워크플로우)

본 계획 strict 안정화 후 시작. 기존 NBA 계획서 Phase 0부터 진행. Maker-Checker 검증·세션 기반 actor·CSRF·rate limit이 모두 살아있는 상태에서 의미 있는 워크플로우 구축.

**총 일정**: A(2주) + B(1.5주) + C(1주 + soft 2주) ≈ **6~7주**. NBA D~ 6~8주를 합치면 **12~15주 (3~3.5개월)**.

---

## 11. 회귀 체크리스트

1. **로그인 성공**: 정상 자격 → 200 + cookie + `csrf_token` 응답.
2. **로그인 실패 (잘못된 비밀번호)**: 401 + 일반화된 메시지. `auth_events.login_failed` 기록.
3. **로그인 실패 (미존재 사용자)**: 응답 시간이 정상 시도와 비교해 ±20% 이내(타이밍 공격 방어). 동일 일반화 메시지.
4. **Rate limit (계정)**: 같은 email로 11번째 실패 → 429 + Retry-After: 300. `account_locked` 기록.
5. **Rate limit (IP)**: 한 IP에서 다른 email로 10회 실패 후 → 429.
6. **Rate limit 리셋**: lockout 만료 후 정상 로그인 가능.
7. **세션 비활동 만료**: 31분 후 요청 → 401 (strict). `last_seen_at` 기준.
8. **세션 절대 만료**: 8시간 1분 후 요청 → 401. (활성 사용 중이라도 강제 만료)
9. **Logout-all**: 다른 디바이스 세션 모두 즉시 무효.
10. **비밀번호 변경**: 다른 모든 세션 자동 종료. 현재 세션은 유지.
11. **비밀번호 강도**: 11자 시도 → 422. 상위 사전 단어 → 422. 마지막 3개 중 재사용 → 422.
12. **초대 토큰 1회용**: 같은 토큰 두 번 사용 → 410 Gone.
13. **초대 토큰 만료**: 24시간 1분 경과 → 410.
14. **CSRF 누락**: POST에 `X-CSRF-Token` 미첨부 → 403 (strict).
15. **CSRF 불일치**: 헤더와 cookie 다름 → 403.
16. **API key 인증**: `Authorization: Bearer crm_xxx` → 200, 사용자 권한 상속.
17. **API key 폐기**: 폐기 후 사용 → 401.
18. **Role 검증 (viewer가 mutation)**: viewer로 POST `/api/sales-notes` → 403.
19. **Role 검증 (crm이 sales-approve)**: crm 단독 사용자가 sales-approve → 403.
20. **Maker-Checker**: 동일인이 crm-approve 후 sales-approve → 422 (`segregation_of_duties`). NBA 계획 회귀 #6과 연동.
21. **Bypass 플래그**: `APPROVAL_ALLOW_SELF_CHECK=true` 환경에서 동일인 sales-approve → 200 + `self_check_bypass` 이벤트.
22. **마지막 admin 박탈 방지**: 유일 admin이 본인 admin role 제거 → 422.
23. **사용자 비활성화**: 비활성화 즉시 모든 세션·API key 무효 → 모든 진행 중 요청 401.
24. **Soft 모드 violation 기록**: AUTH_ENFORCEMENT=soft 상태에서 인증 누락 호출 → 200 통과 + `auth_violation_observed` 기록.
25. **부트스트랩 멱등성**: 부트스트랩 스크립트 두 번째 실행 → no-op.
26. **감사 로그 무결성**: 임의 시나리오 실행 후 `auth_events` 시계열이 실제 발생 순서와 일치.
27. **bcrypt 비교 평균 시간**: 200ms ± 20% 이내 (cost 12 적정성 확인).
28. **CSRF 면제 (API key 경로)**: API key 사용 시 CSRF 헤더 없이도 통과.

---

## 12. 리스크 & 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| 부트스트랩 admin 비밀번호 환경변수 유출 | 시스템 전권 탈취 | 첫 로그인 시 `must_change_password=true` 강제. 환경변수는 시드 후 제거 권장(스크립트 안내). |
| invite_url 카톡/메일 전달 중 유출 | 신규 사용자 계정 탈취 → 탈취자가 비밀번호 설정하면 정상 사용자 잠김 | 24시간 + 1회용. admin이 사용자에게 "URL 받았는지" 확인 후 미수신 시 즉시 재발급. 사용자가 첫 로그인 직후 본인 비밀번호로 변경. 의심 시 admin이 강제 로그아웃 + 재초대. |
| 부트스트랩 부재 (admin 0명 상태로 strict) | 시스템 락아웃 | 부트스트랩 스크립트 실행을 Phase A 배포 직후 의무화. CI 체크: strict 진입 전 `users where 'admin' in roles` 카운트 ≥ 1 검증. |
| Rate limit 메모리 카운터가 재시작 시 리셋 | brute-force 재개 가능 | 단일 인스턴스 가정에서 충분. 재시작이 자주 일어나는 환경이 아님. Phase 3 SSO 시 Redis로 이전. |
| 사전 차단 라이브러리 가용성 (`common-passwords`) | 의존성 실패 시 약한 비밀번호 통과 | 패키지 미설치 시 fail-closed (강도 검증 강제 통과 X — 422 반환). 설치 누락은 헬스체크에서 감지. |
| `escHtml` 점진 교체 중 일부 경로 누락 | XSS 잔존 | Phase B.8에서 모든 렌더링 경로 grep + 코드 리뷰. `data-*` + 이벤트 위임 패턴은 신규만 강제, 기존은 점진적. |
| AUTH_ENFORCEMENT 토글 권한 남용 | 보안 우회 | admin만 가능 + 매 변경 시 `enforcement_change` 이벤트 자동 기록. 환경변수 변경은 배포 권한자만. |
| CSRF 면제 (API key) 오용 | 브라우저에서 API key 탈취 후 호출 | API key는 절대 브라우저에 저장 X 정책 명시. 프론트에서 API key 사용 시도 자체를 코드 리뷰에서 거부. |
| 세션 비활동 30분이 영업 사용 패턴에 짧음 | 사용자 불만 | 도입 후 1주 모니터링. 불만 발생 시 60분으로 조정 가능 (환경변수 `SESSION_IDLE_TIMEOUT_MIN`로 외부화). |
| MFA 미도입 동안의 비밀번호 단독 의존 | 침해 시 즉시 영향 | 결정 G — Phase 3 SSO 마이그레이션을 6~12개월 내 완료 목표. 그 사이 비밀번호 정책 강화로 buffer. |
| CLI(`CRM_CLI_USER` 환경변수) 위변조 | 누군가 admin 슬러그를 박고 CLI 실행 가능 | Phase A는 임시 해법. Phase 2에서 API key + HTTP 호출로 마이그레이션. CLI는 자동화 시나리오 외 사용 자제. |

---

## 13. 요약

NBA 3단계 승인 워크플로우의 가치는 본질적으로 "누가 했는지에 대한 신뢰 가능한 기록"이다. 인증 없이는 이 신뢰가 만들어지지 않으므로, 인증·사용자관리·권한관리는 NBA 워크플로우의 사전 작업이 아니라 **별도의 독립 프로젝트**로 먼저 끝내야 한다.

본 계획은 7가지 사용자 결정을 바탕으로:

- **결정 A** — 세션 기반 + bcrypt cost 12, 8시간 절대/30분 비활동 만료
- **결정 B** — admin URL 핸드오프 초대 (이메일 인프라 X), 24시간 1회용 토큰
- **결정 C** — 분당 10회 시도 / 5분 lockout (계정·IP 양쪽)
- **결정 D** — admin 1클릭 API key 발급, 평문 1회 표시, sha256 hash 저장
- **결정 E** — `AUTH_ENFORCEMENT=off→soft(2주)→strict` 점진 도입
- **결정 F** — admin/crm/sales/viewer 4단계 다중 보유 + Maker-Checker
- **결정 G** — MFA는 Phase 3 Google SSO 마이그레이션 시 한꺼번에

Phase A(인증 인프라 1.5~2주) → B(UI 1~1.5주) → C(엔드포인트 적용 + soft 2주) ≈ **6~7주**. strict 안정화 후 NBA Phase 0 시작 가능. CodeReview.md `#Fix 1·2·4·5`도 동시 해소.

---

## 14. 개정 이력

| 버전 | 날짜 | 변경 |
|---|---|---|
| v1 | 2026-04-25 | 초안 작성. 사용자 7가지 결정 반영. 결정 A~G, DB 4개 테이블, 라우트 매핑, 미들웨어, 위협 모델, 점진 도입 일정, 28개 회귀, 11개 리스크, NBA 계획서와의 인터페이스 명시. |
