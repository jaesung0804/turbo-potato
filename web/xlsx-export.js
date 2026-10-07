/* Small, dependency-free Open XML writer for the dashboard's fixed export schema.
 * ZIP entries are stored, not compressed; no macros or external workbook links.
 * User strings are inline strings, so leading '=' never becomes a formula.
 */
const EstateXlsx = (() => {
  const enc=new TextEncoder();
  const xml=s=>String(s??'').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
  const col=n=>{let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
  const crcTable=Array.from({length:256},(_,i)=>{for(let k=0;k<8;k++)i=(i&1)?0xedb88320^(i>>>1):i>>>1;return i>>>0;});
  function crc(bytes){let c=0xffffffff;for(const b of bytes)c=crcTable[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0;}
  function zip(files){
    const chunks=[],central=[];let offset=0,size=0;
    for(const [name,value] of Object.entries(files)){
      const n=enc.encode(name),b=enc.encode(value),check=crc(b),h=new Uint8Array(30+n.length),v=new DataView(h.buffer);
      v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(6,0x800,true);v.setUint16(12,33,true);
      v.setUint32(14,check,true);v.setUint32(18,b.length,true);v.setUint32(22,b.length,true);v.setUint16(26,n.length,true);h.set(n,30);
      const c=new Uint8Array(46+n.length),d=new DataView(c.buffer);
      d.setUint32(0,0x02014b50,true);d.setUint16(4,20,true);d.setUint16(6,20,true);d.setUint16(8,0x800,true);d.setUint16(14,33,true);
      d.setUint32(16,check,true);d.setUint32(20,b.length,true);d.setUint32(24,b.length,true);d.setUint16(28,n.length,true);d.setUint32(42,offset,true);c.set(n,46);
      chunks.push(h,b);central.push(c);offset+=h.length+b.length;size+=c.length;
    }
    const end=new Uint8Array(22),v=new DataView(end.buffer),count=central.length;
    v.setUint32(0,0x06054b50,true);v.setUint16(8,count,true);v.setUint16(10,count,true);v.setUint32(12,size,true);v.setUint32(16,offset,true);
    const out=new Uint8Array(offset+size+22);let p=0;for(const b of [...chunks,...central,end]){out.set(b,p);p+=b.length;}return out;
  }
  const ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main',rel='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
  function rowHeight(row,columns){
    let lines=1;
    row.forEach((cell,i)=>{if(columns[i]?.style!==7)return;
      const value=String(cell&&typeof cell==='object'?cell.value??'':cell??'');
      const width=Math.max(8,(columns[i].width??14)-3);
      lines=Math.max(lines,value.split('\n').reduce((sum,line)=>sum+Math.max(1,Math.ceil([...line].reduce((n,c)=>n+(c.charCodeAt(0)>255?2:1),0)/width)),0));
    });
    return Math.min(409,Math.max(22,lines*17+8));
  }
  const styles=`<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="${ns}"><numFmts count="3"><numFmt numFmtId="164" formatCode="0.00"/><numFmt numFmtId="165" formatCode="0.0&quot;%&quot;"/><numFmt numFmtId="166" formatCode="0&quot;분&quot;"/></numFmts><fonts count="3"><font><sz val="11"/><color rgb="FF17202A"/><name val="맑은 고딕"/></font><font><b/><sz val="11"/><color rgb="FF17202A"/><name val="맑은 고딕"/></font><font><sz val="11"/><color rgb="FF1D4ED8"/><u/><name val="맑은 고딕"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFFFFF"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF3D6"/></patternFill></fill></fills><borders count="1"><border><left style="thin"><color rgb="FF999999"/></left><right style="thin"><color rgb="FF999999"/></right><top style="thin"><color rgb="FF999999"/></top><bottom style="thin"><color rgb="FF999999"/></bottom></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="8"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="center" horizontal="center"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
  function build(sheets){
    const files={},sheetRefs=[],links=[];
    for(const [i,sheet] of sheets.entries()){
      if(sheet.rows.length>1048575)throw Error('엑셀 최대 행 수를 초과했습니다. 필터 범위를 줄여 주세요.');
      const hyperlinks=[],relationships=[],last=col(sheet.columns.length-1),all=[sheet.columns.map(c=>c.label),...sheet.rows];
      const rows=all.map((row,r)=>`<row r="${r+1}" ht="${r?rowHeight(row,sheet.columns):48}" customHeight="1">${row.map((cell,j)=>{
        const ref=col(j)+(r+1),def=sheet.columns[j]??{},obj=cell&&typeof cell==='object'?cell:{value:cell};
        const value=obj.value,style=r===0?1:(obj.style??(obj.href?6:(def.style??0)));
        if(obj.href&&/^https:\/\//.test(obj.href)){const id='rId'+(relationships.length+1);relationships.push(`<Relationship Id="${id}" Type="${rel}/hyperlink" Target="${xml(obj.href)}" TargetMode="External"/>`);hyperlinks.push(`<hyperlink ref="${ref}" r:id="${id}"/>`);}
        if(obj.formula)return `<c r="${ref}" s="${style}"><f>${xml(obj.formula)}</f>${Number.isFinite(obj.result)?`<v>${obj.result}</v>`:''}</c>`;
        if(value==null || value==='')return `<c r="${ref}" s="${style}"/>`;
        return typeof value==='number'&&Number.isFinite(value)?`<c r="${ref}" s="${style}"><v>${value}</v></c>`:`<c r="${ref}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`;
      }).join('')}</row>`).join('');
      files[`xl/worksheets/sheet${i+1}.xml`]=`<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="${ns}" xmlns:r="${rel}"><dimension ref="A1:${last}${all.length}"/><sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane xSplit="${sheet.freeze??2}" ySplit="1" topLeftCell="${col(sheet.freeze??2)}2" activePane="bottomRight" state="frozen"/></sheetView></sheetViews><cols>${sheet.columns.map((c,j)=>`<col min="${j+1}" max="${j+1}" width="${c.width??14}" customWidth="1"/>`).join('')}</cols><sheetData>${rows}</sheetData><autoFilter ref="A1:${last}${all.length}"/>${sheet.template&&all.length>1?`<conditionalFormatting sqref="F2:F${all.length}"><cfRule type="colorScale" priority="1"><colorScale><cfvo type="num" val="0"/><cfvo type="num" val="45"/><color rgb="FFFFFFFF"/><color rgb="FFF08080"/></colorScale></cfRule></conditionalFormatting><conditionalFormatting sqref="H2:H${all.length}"><cfRule type="colorScale" priority="2"><colorScale><cfvo type="num" val="0.5"/><cfvo type="num" val="2"/><color rgb="FFF08080"/><color rgb="FFFFFFFF"/></colorScale></cfRule></conditionalFormatting>`:""}${hyperlinks.length?'<hyperlinks>'+hyperlinks.join('')+'</hyperlinks>':''}<pageMargins left="0.25" right="0.25" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" paperSize="9"/></worksheet>`;
      if(relationships.length)files[`xl/worksheets/_rels/sheet${i+1}.xml.rels`]=`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${relationships.join('')}</Relationships>`;
      sheetRefs.push(`<sheet name="${xml(sheet.name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`);
      links.push(`<Relationship Id="rId${i+1}" Type="${rel}/worksheet" Target="worksheets/sheet${i+1}.xml"/>`);
    }
    files['xl/styles.xml']=styles;
    files['xl/workbook.xml']=`<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="${ns}" xmlns:r="${rel}"><sheets>${sheetRefs.join('')}</sheets><calcPr fullCalcOnLoad="1"/></workbook>`;
    files['xl/_rels/workbook.xml.rels']=`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${links.join('')}<Relationship Id="rId${sheets.length+1}" Type="${rel}/styles" Target="styles.xml"/></Relationships>`;
    files['_rels/.rels']=`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="${rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
    files['[Content_Types].xml']=`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`;
    return zip(files);
  }
  function download(name,sheets){const url=URL.createObjectURL(new Blob([build(sheets)],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));const a=document.createElement('a');a.href=url;a.download=ResultPages.downloadName(name);a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
  return {build,download,col};
})();
