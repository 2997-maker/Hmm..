"use client";
import { useState } from 'react';
import { Luggage } from 'lucide-react';
import { Combobox, ComboboxInput, ComboboxContent, ComboboxList, ComboboxItem, ComboboxEmpty } from '@/components/ui/combobox';
import { catalog } from '../lib/packing';

const names = [...new Set([...Object.keys(catalog), '헤어오일', '헤어스프레이', '헤어에센스', '샴푸', '린스', '치약', '노트북', '스마트폰', '신발', '시계'])];
export default function ItemSearch({onAdd}:{onAdd:(name:string)=>void}) {
 const [query,setQuery]=useState('');const [message,setMessage]=useState('');
 return <div className="item-search"><label htmlFor="item-autocomplete">물품 이름으로 빠르게 찾기</label><Combobox items={names} value={null} inputValue={query} onInputValueChange={setQuery} onValueChange={(value)=>{if(typeof value==='string'){onAdd(value);setMessage(`${value} 추가 완료`);setQuery('')}}}><ComboboxInput id="item-autocomplete" placeholder="예: 헤 → 헤어오일, 헤어드라이기" autoComplete="off" showClear/><ComboboxContent className="item-search-popup"><ComboboxEmpty>목록에 없어요. 아래 직접 입력으로 추가할 수 있어요.</ComboboxEmpty><ComboboxList>{(item:string)=><ComboboxItem key={item} value={item}><Luggage size={18} aria-hidden="true"/> <span>{item}</span><small>추가</small></ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><p className="helper" role="status">{message||'몇 글자만 입력하고 선택하세요. 방향키와 Enter로도 추가할 수 있어요.'}</p></div>
}
