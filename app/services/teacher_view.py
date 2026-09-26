"""Build the free-teacher view by removing owner/admin diagnostics."""

from __future__ import annotations

import re


def build_teacher_html(html: str) -> str:
    teacher = html.replace("교사 출제 플랫폼", "무료 교사회원 출제 플랫폼", 1)
    # Remove system navigation buttons from desktop and mobile navigation.
    teacher = teacher.replace('<button data-view="system">시스템</button>', '')
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
    return teacher
