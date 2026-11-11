'use strict';
// Display-only copy. Stored estimates, quotations, prompts and policy stay intact.
const UI_REVISION = '11.12-ui1';
function toward(value) {
  const char=Array.from(String(value).trim()).at(-1)||'';
  const code=char.codePointAt(0);
  if(code>=0xac00&&code<=0xd7a3){const final=(code-0xac00)%28;return final&&final!==8?'으로':'로';}
  return ' 쪽으로';
}
function displaySummary(value) {
  return String(value || '')
    .replace(/ 연구 문제의 명시 여부와 별개로 관찰한 검토 흐름이에요\./g,'')
    .replace(/ 연구 문제의 최종 확정을 의미하지는 않아요\./g,'')
    .replace(/^최근 ‘(.+)’ 안의 문제·범위를 좁혀 검토하고 있어요\.$/,'‘$1’의 검토 범위를 좁히고 있어요.')
    .replace(/^최근 정정을 반영해 ‘(.+)’ 쪽으로 검토 관점을 조정하고 있어요\.$/,(_,focus)=>`‘${focus}’${toward(focus)} 검토 관점을 바꿨어요.`)
    .replace(/^‘(.+)’보다 ‘(.+)’에 관심을 두려는 정정이 드러났어요\.$/,(_,from,to)=>`검토 관점을 ‘${from}’에서 ‘${to}’${toward(to)} 바꿨어요.`)
    .replace(/^현재 관심은 ‘(.+)’이며, 관련 관점을 살펴보고 있어요\.$/,'‘$1’ 관련 내용을 살펴보고 있어요.')
    .replace(/^현재 관심은 ‘(.+)’이며, 구체화 정도는 판단 유보 중이에요\.$/,'‘$1’을 검토 중이에요. 구체화 정도는 아직 확인되지 않았어요.')
    .replace(/^‘(.+)’ 안의 문제·범위를 더 살펴보려는 선택이 추가됐어요\.$/,'‘$1’에서 구체화하기를 선택했어요.')
    .replace(/^‘(.+)’(?:과|와) 관련된 관점을 더 둘러보려는 선택이 추가됐어요\.$/,'‘$1’에서 둘러보기를 선택했어요.')
    .replace(/^이번 입력이 추가됐으며, 구체화 정도의 변화는 아직 단정하지 않았어요\.$/,'새로 입력한 내용을 반영했어요. 구체화 정도는 아직 확인되지 않았어요.')
    .trim();
}
function displayStage(value) {
  return ({'범위 좁혀 검토 중':'범위를 좁히는 중','관점 검토 중':'관점 검토 중','관점 조정 중':'관점 조정 중'})[value] || value;
}
function friendlyError(value, area='conversation') {
  const detail=String(value?.error || value?.message || '');
  const code=value?.error_code || value?.code || '';
  const fallback=({papers:'논문을 불러오지 못했어요. 잠시 후 다시 검색해 주세요.',workspace:'탐색을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.',activity:'활동을 저장하지 못했어요. 잠시 후 다시 시도해 주세요.',conversation:'답변을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.'})[area] || '요청을 처리하지 못했어요.';
  const technical=/Gemini|API|HTTP|\.env|JSON|객체 형식|모델|추정된 상태|요청 식별자|서버|터미널|본문/.test(detail);
  let message=detail || (area==='papers'?'논문을 불러오지 못했어요.':'답변을 불러오지 못했어요.');
  if(['upstream_unavailable','response_timeout','connection_timeout','connection_error','incomplete_response','model_error'].includes(code)||technical){
    message=fallback;
  }
  const error=new Error(message);
  error.code=code;error.detail=detail && detail!==message?detail:'';
  return error;
}
