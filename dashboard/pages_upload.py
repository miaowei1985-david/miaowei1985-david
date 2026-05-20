#!/usr/bin/env python3
"""文件上传页面"""
import os, json, time
import html as html_mod
from datetime import datetime

from erp_config import UPLOAD_DIRS, TYPE_LABELS
from dashboard import page, cached_page

def fmt_time(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')

def fmt_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f'{size:.0f}{unit}'
        size /= 1024
    return f'{size:.0f}TB'

def upload_files_list():
    files = []
    for dtype, dpath in UPLOAD_DIRS.items():
        if os.path.isdir(dpath):
            for fn in sorted(os.listdir(dpath), reverse=True):
                if fn.startswith('._'): continue
                fp = os.path.join(dpath, fn)
                if os.path.isfile(fp):
                    st = os.stat(fp)
                    files.append({'name': fn, 'type': TYPE_LABELS.get(dtype, dtype), 'time': fmt_time(st.st_mtime), 'size': fmt_size(st.st_size)})
    return files[:50]


def render_upload():
    files = upload_files_list()
    body = '<h1 style="color:#fff;font-size:22px;margin-bottom:16px;">文件上传</h1>'
    body += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px;margin-bottom:20px;">'
    TEMPLATES = {
        'weight': ('规格品名重量_模版.xlsx', '规格品名重量'),
        'product_cost': ('产品成本_模版.xlsx', '产品成本'),
    }
    for zone_key, label, accept_ext, btn_text, icon in [
        ('finance', '财务对账 CSV', '.csv', '上传财务文件', '📊'),
        ('after_sales', '售后数据 Excel', '.xlsx', '上传售后文件', '📋'),
        ('weight', '规格重量 Excel', '.xlsx', '上传规格文件', '⚖️'),
        ('product_cost', '产品成本 Excel', '.xlsx', '上传成本文件', '💰'),
    ]:
        tmpl_link = ''
        if zone_key in TEMPLATES:
            tmpl_file, tmpl_name = TEMPLATES[zone_key]
            tmpl_link = f'<a href="/templates/{tmpl_file}" style="display:block;text-align:center;font-size:12px;color:#58a6ff;margin-top:6px;text-decoration:none;">下载{tmpl_name}模板</a>'
        body += f'''<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;">
  <h3 style="color:#00d4ff;margin-bottom:8px;">{label}</h3>
  <div class="drop-zone" id="drop_{zone_key}" onclick="document.getElementById('file_{zone_key}').click()">
    <div style="font-size:28px;">{icon}</div><div style="color:#00d4ff;font-size:13px;">点击或拖拽</div>
    <div class="file-name" id="fname_{zone_key}" style="color:#00e676;font-size:13px;margin-top:6px;display:none;"></div>
    <input type="file" id="file_{zone_key}" accept="{accept_ext}" onchange="showName('{zone_key}')" style="display:none;">
  </div>
  <button class="btn" onclick="upload('{zone_key}')" style="display:block;width:100%;padding:10px;border:none;border-radius:6px;background:#238636;color:#fff;font-size:14px;cursor:pointer;margin-top:10px;">{btn_text}</button>
  {tmpl_link}
</div>'''
    body += '</div>'
    body += '<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;"><h3 style="color:#00d4ff;margin-bottom:12px;">已上传文件</h3>'
    body += '<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="border-bottom:1px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">文件名</th><th style="padding:8px;text-align:left;color:#8b949e;">类型</th><th style="padding:8px;text-align:left;color:#8b949e;">时间</th><th style="padding:8px;text-align:left;color:#8b949e;">大小</th></tr></thead><tbody id="file_list">'
    for f in files:
        body += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px 8px;">{html_mod.escape(f["name"])}</td><td style="padding:6px 8px;">{html_mod.escape(f["type"])}</td><td style="padding:6px 8px;">{html_mod.escape(f["time"])}</td><td style="padding:6px 8px;">{html_mod.escape(f["size"])}</td></tr>'
    body += '</tbody></table></div><div id="result" style="display:none;padding:10px;border-radius:6px;margin-top:12px;font-size:13px;"></div>'
    body += """<script>
const FILES={};
function showName(type){const f=document.getElementById('file_'+type).files[0];if(f){FILES[type]=f;const el=document.getElementById('fname_'+type);el.textContent=f.name;el.style.display='block'}}
['finance','after_sales','weight','product_cost'].forEach(type=>{const dz=document.getElementById('drop_'+type);dz.addEventListener('dragover',e=>{e.preventDefault();dz.style.borderColor='#00d4ff';dz.style.background='rgba(0,212,255,0.05)'});dz.addEventListener('dragleave',()=>{dz.style.borderColor='#30363d';dz.style.background=''});dz.addEventListener('drop',e=>{e.preventDefault();dz.style.borderColor='#30363d';dz.style.background='';const f=e.dataTransfer.files[0];if(f){FILES[type]=f;const el=document.getElementById('fname_'+type);el.textContent=f.name;el.style.display='block'}})});
function upload(type){
  const file=FILES[type];
  if(!file){showResult("请先选择文件",false);return}
  const idx=["finance","after_sales","weight","product_cost"].indexOf(type);
  const btns=document.querySelectorAll(".btn");
  if(btns[idx]){btns[idx].disabled=true;btns[idx].textContent="上传中..."}
  
  const CHUNK_SIZE=5*1024*1024; // 5MB每块
  const totalChunks=Math.ceil(file.size/CHUNK_SIZE);
  showProgress(0,file.name+" (分"+totalChunks+"块)");
  
  let uploadedChunks=0;
  
  async function uploadChunk(chunkId){
    const start=chunkId*CHUNK_SIZE;
    const end=Math.min(start+CHUNK_SIZE,file.size);
    const chunk=file.slice(start,end);
    
    const reader=new FileReader();
    reader.onload=async function(){
      const base64=btoa(new Uint8Array(reader.result).reduce((d,b)=>d+String.fromCharCode(b),""));
      
      try{
        const resp=await fetch("/chunk",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({
            chunkId:chunkId,
            filename:file.name,
            data:base64,
            totalChunks:totalChunks,
            type:type
          })
        });
        const d=await resp.json();
        
        if(d.ok){
          uploadedChunks++;
          const pct=Math.round((uploadedChunks/totalChunks)*100);
          showProgress(pct,file.name+" (块"+uploadedChunks+"/"+totalChunks+")");
          
          if(d.merged){
            hideProgress();
            showResult(d.msg,true);
            loadFiles();
            FILES[type]=null;
            document.getElementById("file_"+type).value="";
            document.getElementById("fname_"+type).style.display="none";
            const btns2=document.querySelectorAll(".btn");
            if(btns2[idx]){btns2[idx].disabled=false;btns2[idx].textContent=["上传财务文件","上传售后文件","上传规格文件","上传成本文件"][idx]}
          }else if(chunkId+1<totalChunks){
            uploadChunk(chunkId+1);
          }
        }else{
          hideProgress();
          showResult(d.msg,false);
          const btns2=document.querySelectorAll(".btn");
          if(btns2[idx]){btns2[idx].disabled=false;btns2[idx].textContent=["上传财务文件","上传售后文件","上传规格文件","上传成本文件"][idx]}
        }
      }catch(e){
        hideProgress();
        showResult("上传失败: "+e.message,false);
        const btns2=document.querySelectorAll(".btn");
        if(btns2[idx]){btns2[idx].disabled=false;btns2[idx].textContent=["上传财务文件","上传售后文件","上传规格文件","上传成本文件"][idx]}
      }
    };
    reader.readAsArrayBuffer(chunk);
  }
  
  uploadChunk(0);
}
function showProgress(pct,filename){const el=document.getElementById("progress-container");if(!el){const div=document.createElement("div");div.id="progress-container";div.innerHTML="<div style='margin:12px 0;padding:16px;background:#161b22;border:1px solid #30363d;border-radius:8px;'><div style='color:#8b949e;font-size:13px;margin-bottom:8px;'>上传: "+filename+"</div><div style='background:#21262d;border-radius:4px;height:24px;overflow:hidden;'><div id='progress-bar' style='height:100%;background:linear-gradient(90deg,#238636,#3fb950);width:"+pct+"%;transition:width 0.2s;'></div></div><div id='progress-text' style='text-align:center;color:#3fb950;font-size:16px;font-weight:600;margin-top:8px;'>"+pct+"%</div></div>";document.getElementById("result").before(div)}else{document.getElementById("progress-bar").style.width=pct+"%";document.getElementById("progress-text").textContent=pct+"%"}document.getElementById("result").style.display="none"}
function hideProgress(){const el=document.getElementById("progress-container");if(el)el.remove()}
function showResult(msg,ok){const el=document.getElementById("result");el.textContent=msg;el.style.display="block";el.style.background=ok?"rgba(0,230,118,0.1)":"rgba(255,82,82,0.1)";el.style.color=ok?"#00e676":"#ff5252";setTimeout(()=>el.style.display="none",15000)}
async function loadFiles(){try{const r=await fetch('/files');const data=await r.json();document.getElementById('file_list').innerHTML=data.files.map(f=>'<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px 8px;">'+f.name+'</td><td style="padding:6px 8px;">'+f.type+'</td><td style="padding:6px 8px;">'+f.time+'</td><td style="padding:6px 8px;">'+f.size+'</td></tr>').join('')}catch(e){}}
</script>"""
    return page('文件上传', 'upload', body)
