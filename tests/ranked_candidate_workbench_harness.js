/* Exercise real rendered workbench handlers with an isolated DOM/API double. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
class Element {
  constructor(tag='div') { Object.assign(this, {tag,children:[],listeners:{},value:'',checked:false,disabled:false,textContent:''}); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=[...items]; }
  setAttribute(k,v) { this[k]=v; }
  addEventListener(k,v) { this.listeners[k]=v; }
}
const elements = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element()]));
Object.assign(elements.verificationKind,{value:'EVIDENCE'});
Object.assign(elements.verificationScope,{value:'ANSWER,EXPLANATION,ACTUAL_CAUSE'});
Object.assign(elements.criteriaRef,{value:'frozen-before-review'});
elements.conditionObservations.value='[]';
const calls=[];let batch=null;const digest='d'.repeat(64);
const fetch=async(url,opt)=>{
  const body=opt.body?JSON.parse(opt.body):null;calls.push({url,body});let value;
  if(url.includes('/candidate-cause-map')) value={condition_observations:[{condition_key:'agreement',status:'UNKNOWN'}],graph_parameters_trained:false};
  else if(url.includes('/candidate-evidence/revoke')) value={revocation:{affected_candidate_uses:[{}]},withdrawn_segment_count:1};
  else if(url.includes('/candidate-policy')) value={revision:0,effective_max_rank:3,learned_reward_count:0,learning_kind:'VERIFIED_RECEIPT_CASE_STATISTICS'};
  else if(url.includes('/review')) {batch.reviews[body.candidate_id]={...body,status:body.outcome};value=batch.reviews[body.candidate_id];}
  else if(url.includes('/candidate-batches')&&body) { batch={batch_id:'test-batch',digest,candidates:body.candidates,manifest:{context_ref:body.context_ref,selected_candidate_ids:body.candidates.map(c=>c.candidate_id),verification_criteria:body.verification,search:{}},reviews:{}};value=batch;}
  else if(url.includes('/candidate-batches')) value={batches:batch?[batch]:[]};
  else throw new Error('Unexpected request: '+url);
  return {ok:true,status:200,json:async()=>value};
};
const allNodes=(node)=>[node,...node.children.flatMap(child=>allNodes(child))];
const findLabel=(node,needle)=>allNodes(node).find(n=>n.tag==='label'&&n.children.some(c=>c.textContent.includes(needle)));
(async()=>{
  vm.runInNewContext(html.split('<script>')[1].split('</script>')[0],{document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag)},location:{origin:'https://test.invalid'},URL,fetch,confirm:()=>true,console,Promise,Set,Number,JSON,String,encodeURIComponent});
  await new Promise(setImmediate);
  elements.useCauseMap.checked=true;elements.focusNodes.value='goal';elements.conditionObservations.value='[{"condition_key":"agreement","status":"UNKNOWN"}]';
  await elements.causeMapButton.listeners.click();assert(elements.causeMapDetail.textContent.includes('UNKNOWN'));
  await elements.createButton.listeners.click();
  const created=calls.find(c=>c.url.includes('/candidate-batches')&&c.body);
  assert.deepEqual(created.body.verification.required_dimensions,['ANSWER','EXPLANATION','ACTUAL_CAUSE']);
  assert.equal(created.body.cause_map_request.observations[0].status,'UNKNOWN');
  const card=elements.batches.children[0].children.filter(n=>n.tag==='article').at(-1);
  const form=card.children.at(-1);const labels=form.children.filter(n=>n.tag==='label');
  labels[0].children.at(-1).value='ACCEPTED';labels[1].children.at(-1).value='VERIFIED';
  labels[2].children.at(-1).value='checked-reason';labels[3].children.at(-1).value='verification:v1';
  findLabel(form,'교사가 이 후보').children[0].checked=true;
  for(const fieldset of form.children.filter(n=>n.tag==='fieldset')) {
    fieldset.children[1].children.at(-1).value='PASSED';fieldset.children[2].children.at(-1).value='dimension-evidence:v1';fieldset.children[3].children[0].checked=true;
    if(fieldset.children.length>4) {fieldset.children[4].children.at(-1).value='INDEPENDENT_RETEST';fieldset.children[5].children.at(-1).value='observed-attempt:v1';fieldset.children[6].children.at(-1).value='unaided-separate-attempt:v1';}
  }
  await form.children.at(-2).children[0].listeners.click();const reviewed=calls.find(c=>c.url.includes('/review')).body;
  assert.equal(reviewed.answer_check.status,'PASSED');assert.equal(reviewed.explanation_check.status,'PASSED');assert.equal(reviewed.actual_cause_check.contrast_ref,'unaided-separate-attempt:v1');
  assert.equal(batch.candidates.at(-1).rank,14,'review must preserve original model rank');
  elements.revokedEvidence.value='verification:v1';elements.correctionEvidence.value='correction:v2';elements.correctionReason.value='checked correction';
  await elements.revokeEvidenceButton.listeners.click();assert.equal(calls.filter(c=>c.url.includes('/candidate-evidence/revoke')).length,0);
  elements.correctionChecked.checked=true;await elements.revokeEvidenceButton.listeners.click();
  assert.equal(calls.filter(c=>c.url.includes('/candidate-evidence/revoke')).length,1);
  assert(calls.every(c=>c.url.includes('teacher_token=')));
  console.log('PASS: fixed graph context; unknown preserved; separate answer/explanation/cause checks; original rank; explicit evidence withdrawal.');
})().catch(error=>{console.error(error);process.exitCode=1;});
