import glob,sys,os,json,collections,xml.etree.ElementTree as ET
S=sys.argv[1]
def load(side):
    d={};files={}
    xs=glob.glob(f"{S}/res/{side}/*.xml")+(glob.glob(f"{S}/res/base_shards/*.xml") if side=="base" else [])
    if side=="base":
        xs=[x for x in xs if not x.endswith("test_command_adapters_router.py.xml")]
    for x in xs:
        for tc in ET.parse(x).getroot().iter("testcase"):
            cn=tc.get("classname","");nm=tc.get("name")
            st="passed"
            for c in tc:
                if c.tag in("failure","error"):st="failed"
                elif c.tag=="skipped":st="skipped"
            d[f"{cn}::{nm}"]=st
    return d
b,c=load("base"),load("cand")
cnt=lambda d:dict(collections.Counter(d.values()))
print("base",len(b),cnt(b));print("cand",len(c),cnt(c))
bo=sorted(set(b)-set(c));co=sorted(set(c)-set(b))
ch=sorted(k for k in set(b)&set(c) if b[k]!=c[k])
print("base-only",len(bo),"cand-only",len(co),"changed",len(ch))
for k in ch:print(" ",k,b[k],"->",c[k])
print("cand-only:");[print(" ",k,c[k]) for k in co]
print("base-only status",cnt({k:b[k] for k in bo}))
print("cand-only status",cnt({k:c[k] for k in co}))
# node level tsv
import gzip
with gzip.open(f"{S}/nodes.tsv.gz","wt") as f:
    for k in sorted(set(b)|set(c)):f.write(f"{k}\t{b.get(k,'-')}\t{c.get(k,'-')}\n")
