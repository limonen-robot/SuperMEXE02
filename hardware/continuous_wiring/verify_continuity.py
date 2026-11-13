"""Prove continuity from wire geometry, ignoring all net labels and power names."""
import json,math,re,sys
from pathlib import Path
from xml.etree import ElementTree as ET

def parse(text):
    stack=[];root=None
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^()\s]+',text):
        if token=='(':
            node=[]
            if stack:stack[-1].append(node)
            else:root=node
            stack.append(node)
        elif token==')':stack.pop()
        else:stack[-1].append(json.loads(token) if token.startswith('"') else token)
    assert not stack
    return root
def children(t,name):return [n for n in t if isinstance(n,list) and n and n[0]==name]
def child(t,name):return next(iter(children(t,name)),None)
def value(t,name):return child(t,name)[1]
def grid(p):
    q=tuple(round(float(v)/1.27) for v in p)
    assert all(abs(float(v)-i*1.27)<.002 for v,i in zip(p,q)),p
    return q
def topology(xml):
    return {frozenset((p.attrib['ref'],p.attrib['pin']) for p in n.findall('node')) for n in ET.parse(xml).findall('./nets/net')}

def verify(schematic,netlist):
    t=parse(Path(schematic).read_text(encoding='utf-8'))
    assert not children(t,'global_label') and not children(t,'sheet')
    labels=[n[1] for n in children(t,'label')]
    assert len(labels)==len(set(labels)), 'Repeated local labels could conceal a disconnected wire'
    library={s[1]:s for s in children(child(t,'lib_symbols'),'symbol')}
    pins={}
    for s in children(t,'symbol'):
        ref=next(p[2] for p in children(s,'property') if p[1]=='Reference')
        at=child(s,'at');sx,sy,ang=map(float,at[1:4]);rad=math.radians(ang)
        mirror=child(s,'mirror');mirror=mirror[1] if mirror else None
        for unit in children(library[value(s,'lib_id')],'symbol'):
            for p in children(unit,'pin'):
                px,py=map(float,child(p,'at')[1:3])
                dx=px*math.cos(rad)-py*math.sin(rad);dy=-(px*math.sin(rad)+py*math.cos(rad))
                if mirror=='x':dy=-dy
                elif mirror=='y':dx=-dx
                pins[(ref,value(p,'number'))]=grid((sx+dx,sy+dy))
    wires=[];points=set(pins.values())
    for w in children(t,'wire'):
        a,z=[grid(x[1:3]) for x in children(child(w,'pts'),'xy')]
        assert a[0]==z[0] or a[1]==z[1], 'Use orthogonal wire segments'
        wires.append((a,z));points.update((a,z))
    points.update(grid(child(n,'at')[1:3]) for n in children(t,'junction'))
    parent={p:p for p in points}
    def find(x):
        while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
        return x
    def join(a,z):parent[find(z)]=find(a)
    for a,z in wires:
        if a[0]==z[0]:on=[p for p in points if p[0]==a[0] and min(a[1],z[1])<=p[1]<=max(a[1],z[1])]
        else:on=[p for p in points if p[1]==a[1] and min(a[0],z[0])<=p[0]<=max(a[0],z[0])]
        for p in on:join(a,p)
    expected=topology(netlist);actual={}
    for key in set().union(*expected):actual.setdefault(find(pins[key]),set()).add(key)
    drawn={frozenset(v) for v in actual.values()}
    assert drawn==expected, ('Wire-only groups differ from electrical nets',expected-drawn,drawn-expected)
    print(f'PASS: {len(wires)} wire segments; every exported net is connected by geometry alone; no label aliases or child sheets.')
    return expected

if __name__=='__main__':
    if len(sys.argv) not in [3,4]:raise SystemExit('Usage: python verify_continuity.py diagram.kicad_sch exported.net.xml [original.net.xml]')
    result=verify(sys.argv[1],sys.argv[2])
    if len(sys.argv)==4:
        assert result==topology(sys.argv[3]),'Connectivity differs from the original diagram'
        print('PASS: all component-pin net groups match the original diagram.')
