// 문서의 mermaid 블록이 **실제로 렌더되는지** 검증한다.
//
//   make diagrams
//
// 🔴 정적 괄호 검사만으로는 부족하다 — 괄호 개수가 맞아도 순서가 틀리면
//    ( 예: RI[("라벨"])  vs  RI[("라벨")] ) 파서가 거부한다. 실제로 그 버그를 냈다.
//    mermaid 는 DOM 을 요구하므로 jsdom 으로 최소 DOM 을 만들어 파서만 돌린다.
//
// 의존성은 저장소에 넣지 않는다 — Makefile 이 임시 디렉토리에 설치해 쓴다.
import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual:true });
for (const k of ['window','document','Element','SVGElement','HTMLElement','DOMParser',
                 'Node','NodeFilter','getComputedStyle','MutationObserver']) {
  try { Object.defineProperty(global, k, { value: k==='window'?dom.window:dom.window[k],
        configurable:true, writable:true }); } catch {}
}
try { Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true}); } catch {}
const { default: mermaid } = await import('mermaid');
mermaid.initialize({ startOnLoad:false, securityLevel:'loose' });
const ROOT = process.argv[2] || 'docs';
let n=0, bad=0;
// 🔴 재귀로 훑는다. docs/ 가 reco/design/ · convention/ 처럼 하위로 갈라져 있어
//    최상위만 보면 문서를 하나도 못 찾고 "총 0개" 로 조용히 통과한다.
for (const f of fs.readdirSync(ROOT,{recursive:true}).filter(x=>x.endsWith('.md'))){
  const s=fs.readFileSync(path.join(ROOT,f),'utf8');
  const blocks=[...s.matchAll(/```mermaid\n([\s\S]*?)```/g)].map(m=>m[1]);
  for (let i=0;i<blocks.length;i++){
    n++;
    try { await mermaid.parse(blocks[i]); console.log(`  ✅ ${f} #${i+1}  (${blocks[i].split('\n').length}줄)`); }
    catch(e){ bad++; console.log(`  ❌ ${f} #${i+1}\n     ${String(e.message||e).split('\n').slice(0,6).join('\n     ')}`); }
  }
}
console.log(`\n총 ${n}개 · 실패 ${bad}개`);
process.exit(bad?1:0);
