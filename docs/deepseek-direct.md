# 딥시크 직접 호출

Railway 운영 서비스에서 `EDURECOVER_DEEPSEEK_DIRECT_ENABLED=true`를 설정하면 다음 두 모델을 딥시크 공식 API로 직접 요청합니다.

| 화면 모델 | 공식 호출 이름 |
|---|---|
| DeepSeek-V4.1-Flash | deepseek-flash |
| DeepSeek-V4-Pro | deepseek-v4-pro |

서버에 `DEEPSEEK_API_KEY`가 있어야 합니다. 인증 키는 Railway 비밀 환경변수에만 저장하며 문서, 코드, 사용자 입력창, 응답에는 넣지 않습니다. `OPENROUTER_API_KEY`는 딥시크의 인증 키를 대신하지 않습니다.

직접 연결을 켜면 기존 두 모델의 연결을 공식 직접 경로로 바꾸고, 다른 딥시크 모델의 OpenRouter 경유 연결을 제외합니다. 나머지 계열의 연결은 보존합니다. 이전 R1/V3.2/V4 Flash를 새 Flash와 같은 모델로 바꾸어 표시하지 않습니다.

이 두 직접 경로에는 무보관 조건(ZDR)을 요구하지 않습니다. 딥시크의 보관·처리 조건이 적용됩니다. GPT-6 Luna는 기존 Azure 경유·무보관 필수·우회 금지 조건을 유지합니다. 소유자 로그인과 제공자 사용 허용은 모든 호출에 계속 적용됩니다.

운영 화면은 `/owner/open-models`이며, `/owner/open-models/call`로 직접 질문을 보냅니다. 화면의 인증 정보 필요/호출 설정됨 상태는 서버 설정을 나타내며 실제 호출 성공과 구별합니다. 서버 인증 키를 연결한 후 소유자가 짧은 시험 질문을 보내 실제 응답을 확인해야 합니다.

## 이용조건 확인: 2026-10-17 한국시간

딥시크 Open Platform 약관(2026-04-29 시행) 4.2는 다른 모델 학습과 모델 증류를 출력물 활용 예로 명시합니다. 법률 준수, 입력 권리, 이용약관 및 일반 사용 제한은 계속 적용됩니다. 공개 모델 가중치의 사용허락과 API 서비스 계약은 별개입니다.

RAG는 외부 자료를 검색해 질문에 넣는 방식이며 모델 가중치를 학습시키는 작업과 다릅니다. 이 변경은 학습이나 추가 서버 설치를 실행하지 않습니다.

공식 출처:
- https://api-docs.deepseek.com/
- https://api-docs.deepseek.com/updates/
- https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html
