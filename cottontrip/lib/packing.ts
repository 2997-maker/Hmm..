export type PackItem={id:string,name:string,kind:string,qty:number,location:string,options:string[],comparison?:string,ratio?:string,spec?:string};
export const catalog:Record<string,{weight:number,emoji:string,note:string}>={
 '보조배터리':{weight:.25,emoji:'🔋',note:'배터리 용량과 단자 보호, 휴대 위치에 관한 항공사 조건을 확인해 주세요.'},
 '운동화':{weight:.7,emoji:'👟',note:'신발 1켤레 기준 시험용 무게입니다. 반입 기준 검증은 아직 완료되지 않았어요.'},
 '샌들':{weight:.35,emoji:'🩴',note:'신발 1켤레 기준 시험용 무게입니다. 실제 제품 무게를 확인해 주세요.'},
 '구두':{weight:.8,emoji:'👞',note:'신발 1켤레 기준 시험용 무게입니다.'},
 '세면도구':{weight:.5,emoji:'🧴',note:'세면도구에 포함된 액체의 종류·용량·포장 조건을 개별적으로 확인해 주세요.'},
 '선크림':{weight:.12,emoji:'🧴',note:'용기 용량과 액체류 포장 조건을 확인해 주세요.'},
 '속옷':{weight:.08,emoji:'🧺',note:'종류별 시험용 대표 무게만 계산했어요. 공식 반입 기준은 미확인입니다.'},
 '양말':{weight:.05,emoji:'🧦',note:'양말 1켤레 기준 시험용 무게입니다.'},
 '칫솔':{weight:.03,emoji:'🪥',note:'전동 제품인 경우 배터리 조건을 별도로 확인해 주세요.'},
 '충전기':{weight:.15,emoji:'🔌',note:'배터리가 포함된 제품인지 확인해 주세요.'},
 '우산':{weight:.35,emoji:'🌂',note:'제품 크기와 항공사 휴대 조건을 확인해 주세요.'},
 '헤어드라이기':{weight:.55,emoji:'💨',note:'제품 종류를 확인해 주세요. 시험용 무게만 계산했어요.'},
 '스프레이':{weight:.2,emoji:'🧴',note:'내용물·용량·가연성 여부에 따라 조건이 달라질 수 있어요. 제품별 확인이 필요합니다.'},
 '휴대용 고데기':{weight:.3,emoji:'🔌',note:'유선·배터리·가스 등 전원 방식과 분리 가능 여부를 확인해 주세요.'},
 '손목시계':{weight:.08,emoji:'⌚',note:'제품의 전원 방식에 따라 필요한 조건을 확인해 주세요.'},
 '스마트워치':{weight:.06,emoji:'⌚',note:'내장 배터리와 휴대 조건을 확인해 주세요.'},
 '알람시계':{weight:.25,emoji:'⏰',note:'제품 종류·배터리를 확인해 주세요.'},
 '벽걸이시계':{weight:.7,emoji:'🕒',note:'크기·배터리·파손 우려를 확인해 주세요.'}
};
export function parseItems(raw:string):PackItem[]{return raw.split(/[,\n、]+/).map(v=>v.trim()).filter(Boolean).slice(0,80).map((s)=>{const match=s.match(/\s*(\d+)\s*(?:개|켤레|벌|대|쌍)?\s*$/);const qty=Math.min(99,Math.max(1,match?Number(match[1]):1));let name=(match?s.slice(0,match.index):s).trim();if(!name)name=s;if(name==='팬티')name='속옷';if(name==='고데기')name='휴대용 고데기';const options=name==='시계'?['손목시계','스마트워치','알람시계','벽걸이시계','모르겠음']:name==='신발'?['샌들','구두','운동화','모르겠음']:catalog[name]?[name,'모르겠음']:['모르겠음'];return {id:crypto.randomUUID(),name,kind:catalog[name]?name:'모르겠음',qty,location:'기내',options};});}
export function itemWeight(i:PackItem):number|null {if(catalog[i.kind])return catalog[i.kind].weight;if(i.comparison&&catalog[i.comparison]&&i.ratio&&i.ratio!=='모르겠음')return catalog[i.comparison].weight*({'절반 정도':.5,'비슷함':1,'두 배 정도':2}[i.ratio]??1);return null;}
