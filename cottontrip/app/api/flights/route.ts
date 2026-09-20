export async function GET(request:Request){
 const params=new URL(request.url).searchParams;const direction=params.get('direction');const date=params.get('date')||'';
 const reply=(message:string,flights:unknown[]=[])=>Response.json({message,flights},{headers:{'Cache-Control':'no-store'}});
 if(!['arrivals','departures'].includes(direction||'')||!/^\d{4}-\d{2}-\d{2}$/.test(date)||Number.isNaN(Date.parse(date)))return reply('여행 날짜를 확인해 주세요.');
 let key=process.env.AIRPORT_API_KEY?.trim();
 if(!key)return reply('항공편 인증키가 아직 연결되지 않았어요. 항공사를 직접 입력해 계속할 수 있습니다.');
 if(key&&/%[0-9a-f]{2}/i.test(key)){try{key=decodeURIComponent(key);}catch{return reply('인증키 형식을 확인해 주세요. 직접 입력으로 계속할 수 있습니다.');}}
 const endpoint=direction==='arrivals'?'getSPaxFlt4TripPlatformArrivals':'getSPaxFlt4TripPlatformDepartures';
 try{let rows:Record<string,string>[]=[];let total=1;for(let page=1;rows.length<total&&page<=20;page++){
 const url=new URL(`https://apis.data.go.kr/B551177/statusOfSPaxFlt4TripPlatform/${endpoint}`);url.search=new URLSearchParams({serviceKey:key,type:'json',airportCode:'NRT',numOfRows:'100',pageNo:String(page)}).toString();
 const r=await fetch(url,{signal:AbortSignal.timeout(10000),cache:'no-store'});if(!r.ok)return reply('항공편 조회에 실패했어요. 직접 입력으로 진행해 주세요.');
 const d:any=await r.json();const root=d.response||d;if(String(root.header?.resultCode)!=='00')return reply('항공편 제공 서비스가 응답하지 못했어요. 직접 입력해 주세요.');const body=root.body;const raw=body?.items?.item||body?.items||[];const pageRows=Array.isArray(raw)?raw:raw.flightId?[raw]:[];rows.push(...pageRows);total=Number(body?.totalCount)||rows.length;if(!pageRows.length)break;
 }
 const day=['ynSun','ynMon','ynTue','ynWed','ynThu','ynFri','ynSat'][new Date(date+'T12:00:00+09:00').getUTCDay()];const compact=date.replaceAll('-','');const seen=new Set<string>();
 const flights=rows.filter(f=>f.airportCode==='NRT'&&f.firstdate?.replaceAll('-','')<=compact&&f.lastdate?.replaceAll('-','')>=compact&&f[day]==='Y').filter(f=>{const k=f.flightId+f.st;if(seen.has(k))return false;seen.add(k);return true}).map(f=>({flightId:f.flightId,airline:f.airline,st:f.st,codeshare:f.codeshare,masterFlightId:f.masterFlightId}));
 return reply(flights.length?'정기 운항 일정입니다. 실제 운항은 항공사에서 확인해 주세요.':'해당 날짜의 지원 항공편을 찾지 못했어요. 항공사를 직접 입력해 주세요.',flights);
 }catch{return reply('항공편 연결을 확인하지 못했어요. 직접 입력으로 계속 진행해 주세요.');}
}
