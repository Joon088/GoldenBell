# FiveM 골든벨 정산 봇

## 기능
- `/피티 횟수`
  - 10분 = +1회
  - 20분 = +2회
  - 1회당 50,000,000원
  - 되돌리기 = 본인의 마지막 기록 취소
  - 목표 횟수 도달 시 자동 마감
- `/스테로이드 횟수`
  - 1회 등록 = +1회
  - 1회당 20,000,000원
  - 목표 횟수 도달 시 자동 마감
- `/계좌등록 @유저 계좌번호`
- `/계좌확인 @유저`

최종 정산은 다음 형식으로 표시됩니다.

`@닉네임 | 계좌번호 | 횟수 | 정산금액`

## 배포 구조
GitHub 저장소 → Railway 서비스 자동 배포
Railway PostgreSQL → 계좌/티켓/기록 영구 저장

## Railway 설정
Railway 프로젝트에서:
1. GitHub Repository 연결
2. PostgreSQL 서비스 추가
3. 봇 서비스 Variables에 아래 값 등록
   - `DISCORD_TOKEN`
   - `DATABASE_URL`
   - `GUILD_ID`
   - `PT_CHANNEL_ID`
   - `STEROID_CHANNEL_ID`

PostgreSQL의 DATABASE_URL은 Railway 변수 참조 기능을 사용해 연결할 수 있습니다.

## Discord Developer Portal
Bot 권한:
- View Channels
- Send Messages
- Embed Links
- Read Message History
- Use Application Commands

Developer Portal에서 Server Members Intent도 활성화하는 것을 권장합니다.

## 채널 ID
디스코드 개발자 모드 활성화 후:
채널 우클릭 → 채널 ID 복사

`PT_CHANNEL_ID=...`
`STEROID_CHANNEL_ID=...`

값을 0으로 두면 채널 제한을 사용하지 않습니다.

## 기존 진행분 수기 이관
- `/수기등록 티켓번호 @유저 횟수`
  - 관리자 전용
  - 진행 중인 티켓에 해당 유저의 기존 횟수를 추가
  - 목표 횟수 도달 시 자동 마감
- `/수기차감 티켓번호 @유저 횟수`
  - 관리자 전용
  - 잘못 등록한 횟수를 원하는 만큼 차감
  - 해당 유저가 보유한 횟수보다 많이 차감할 수 없음

수기등록/차감 후 원래 골든벨 티켓 임베드도 즉시 갱신됩니다.

## 티켓번호 표시
모든 골든벨 임베드 하단에 `티켓번호 #숫자`가 항상 표시됩니다.
진행 중 / 수동 마감 / 자동 마감 후에도 같은 티켓번호가 유지됩니다.

예: `/수기등록 티켓번호:12 유저:@직원 횟수:5`

## v4 변경사항
티켓번호를 하단 푸터뿐 아니라 임베드 상단 첫 필드에도 표시합니다.
예: `🎫 티켓번호  #12`
