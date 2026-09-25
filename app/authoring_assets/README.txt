[PRIVATE / SERVER-ONLY] EduRecover authoring assets

목적
- AI 출제 에이전트의 문항 생성
- 별도 AI 검증 에이전트의 교차검증

외부 AI 전달 원칙
- 전체 내부 원본은 EduRecover 서버에 보관한다.
- 선택된 AI에는 해당 작업에 필요한 출제자료·출제지침 부분만 전달한다.
- 플랫폼 채팅기록, 다른 고객 자료, API 키, 내부 로그는 전달하지 않는다.

탑재 자료
- vocab_norm_v1.txt : 구체 정규화 어휘 VOC-ID 자산
- syntax_master_v1.txt : 구체 구문과 고1·고2·고3 구문 범위
- grade_syntax_3y.txt : 3개년 실측 학년별 구문난도
- english_authoring_rules.txt : 영어 문항 출제·검수 지침
- authoring_wizard_canonical.txt : 출제마법사 단일 기준
- distractor_principles_35.txt : 오답선지 35개 원리
- current_policy.txt : 현재 어휘·구문·출제 정책

주의
- 복구된 vocab_norm_v1.txt에는 개별 표제어의 검증된 고1/고2/고3 태그가 없다.
- 따라서 서버는 학년별 단어 태그를 임의 생성하지 않는다.
- 학년 적합성은 별도 어휘 게이트와 출처가 확인된 자료로 판정한다.
