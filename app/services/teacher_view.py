"""Build the free-teacher view by removing owner/admin diagnostics."""

from __future__ import annotations

import re


def build_teacher_html(html: str) -> str:
    teacher = html.replace("교사 출제 플랫폼", "무료 교사회원 출제 플랫폼", 1)
    # Remove system navigation buttons from desktop and mobile navigation.
    teacher = teacher.replace('<button data-view="system">시스템</button>', '')
    # The full classroom product has its own teacher login. Keep the free
    # workspace's owner-only tutor controls hidden and link to that product.
    teacher = teacher.replace('<button data-view="tutor">AI 튜터</button>', '')
    teacher = teacher.replace('<button data-view="tutor">AI튜터</button>', '')
    teacher = teacher.replace('<button data-view="media">시청각 자료</button>', '')
    teacher = teacher.replace('<button data-view="media">시청각</button>', '')
    teacher = re.sub(
        r'<a id="classroomProductLink".*?</a>',
        '<a id="classroomProductLink" class="btn dark" href="/classroom" '
        'style="text-decoration:none">교사용 전자칠판·시청각 수업</a>',
        teacher,
    )
    teacher = re.sub(
        r'<section id="(?:tutor|media)" class="view">.*?</section>',
        '',
        teacher,
        flags=re.S,
    )
    # Remove workspace shortcuts to sections absent from the teacher page.
    teacher = re.sub(r'<button[^>]*onclick="showView\(\'tutor\'\)"[^>]*>.*?</button>', '', teacher)
    # Remove owner/admin system section.
    teacher = re.sub(
        r'<section id="system" class="view">.*?</section>',
        '',
        teacher,
        flags=re.S,
    )
    # Do not enumerate internal authoring asset titles for teacher members.
    teacher = re.sub(
        r'<div class="card" style="margin-top:12px;padding:12px">\s*'
        r'<strong>적용 출제자료 및 출제지침</strong>\s*'
        r'<div id="authoringAssetsList" class="muted" style="margin-top:8px">.*?</div>\s*'
        r'</div>',
        '<div class="callout" style="margin-top:12px">EduRecover 내부 출제·검수 기준을 작업별 최소 범위로 적용합니다.</div>',
        teacher,
        flags=re.S,
    )
    # Remove owner-only privacy/ZDR diagnostics from the teacher status display.
    teacher = re.sub(
        r'\s*const privacy=d\.privacy_gate\?\.providers\|\|\{\};.*?'
        r'if\(cell\) cell\.innerHTML=.*?;\n',
        '\n    if(cell) cell.innerHTML="<span class=\\\'badge "+(anyReady?"good":"warn")+"\\\'>"+parts.join(" · ")+"</span>";\n',
        teacher,
        flags=re.S,
    )
    # Skip owner/admin refresh calls in the teacher browser.
    teacher = teacher.replace('refreshAuthoringAssets();', '')
    teacher = teacher.replace('refreshOwnerControl();', '')
    teacher = teacher.replace(
        "<body>",
        '<body><aside style="padding:12px 20px;background:#edf4ff;color:#172b45">'
        '교사 작업실에서 문항 편집·출력을 사용할 수 있습니다. AI 자동출제·교차검증은 '
        '<a href="/owner/login">관리자 로그인</a> 후 이용해 주세요.</aside>',
        1,
    )
    return teacher
