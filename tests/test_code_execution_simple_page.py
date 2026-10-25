"""One-button runner page: single button, file pick, run and result flow."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.code_execution_workspace import code_execution_simple_html

HARNESS = r'''const src=require('fs').readFileSync(process.argv[2],'utf8');
const els={};function el(id){return els[id]||(els[id]={id,textContent:'',value:'',disabled:false,hidden:false,className:'',files:[],children:[],handlers:{},
 replaceChildren(...c){this.children=c},addEventListener(t,f){this.handlers[t]=f}});}
global.document={getElementById:el,createElement:()=>({textContent:''})};
global.setTimeout=(f)=>Promise.resolve().then(f);
const calls=[];let polls=0;
global.fetch=async(url,opt={})=>{calls.push({url,opt});let d;
 if(url.endsWith('/status'))d={configured:true};
 else if(url.endsWith('/runs')&&opt.method==='POST')d={id:'r1',status:'queued',message:'실행 대기 중입니다.'};
 else if(url.endsWith('/runs/r1')){polls++;d=polls<2?{id:'r1',status:'running'}:{id:'r1',status:'completed',exit_code:0,stdout:'== check\n점검 통과',stderr:'',message:'실행이 끝났습니다.'};}
 return {ok:true,status:200,json:async()=>d};};
eval(src);
(async()=>{const tick=()=>new Promise(r=>setImmediate(r));for(let i=0;i<5;i++)await tick();
 const assert=(c,m)=>{if(!c){console.log('실패:',m);process.exit(1)}};
 assert(el('run').disabled===true,'파일 전에는 실행 비활성');
 el('pick').files=[{name:'team life tool.py',text:async()=>'print(1)'}];await el('pick').handlers.change();
 assert(el('command').value==="python3 'team life tool.py'",'명령 자동 채움: '+el('command').value);
 assert(el('run').disabled===false,'파일 고른 뒤 실행 활성');
 await el('run').handlers.click();for(let i=0;i<20;i++)await tick();
 const post=calls.find(c=>c.opt.method==='POST');const body=JSON.parse(post.opt.body);
 assert(body.explicit_authorization===true&&body.files[0].path==='team life tool.py'&&body.files[0].content==='print(1)','전송 내용');
 assert(el('result-title').textContent==='결과 · 성공','결과 제목: '+el('result-title').textContent);
 assert(el('stdout').textContent.includes('점검 통과'),'출력 표시');
 assert(el('stderr-box').hidden===true,'오류 없음 숨김');
 // 큰 파일 거부
 el('pick').files=[{name:'big.py',text:async()=>'x'.repeat(130*1024)}];await el('pick').handlers.change();
 assert(el('status').className==='error'&&el('status').textContent.includes('너무 큽니다'),'큰 파일 거부');
 console.log('모든 화면 시험 통과');})();
'''


class SimpleRunnerPageTests(unittest.TestCase):
    def test_single_button_and_advanced_link(self):
        html = code_execution_simple_html()
        self.assertEqual(len(re.findall(r"<button", html)), 1)
        self.assertIn('/owner/code-workspace/advanced', html)
        self.assertNotIn("innerHTML", html)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_pick_run_and_result_flow(self):
        script = re.search(r"<script>(.*?)</script>", code_execution_simple_html(), re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            page, harness = Path(tmp, "page.js"), Path(tmp, "harness.js")
            page.write_text(script, encoding="utf-8")
            harness.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run(["node", str(harness), str(page)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("모든 화면 시험 통과", result.stdout)


if __name__ == "__main__":
    unittest.main()
