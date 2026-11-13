"""Check a KiCad XML netlist against the user's recorded physical wiring."""
import sys
from xml.etree import ElementTree as ET

def verify(path):
    tree=ET.parse(path)
    nets={n.attrib['name']:{(p.attrib['ref'],p.attrib['pin']) for p in n.findall('node')} for n in tree.findall('./nets/net')}
    owner={p:name for name,nodes in nets.items() for p in nodes}
    def same(*pins):
        names={owner[p] for p in pins}
        assert len(names)==1, (pins,names)
        return nets[next(iter(names))]
    def exact(*pins):
        assert same(*pins)==set(pins),pins
    groups={}
    for group,ps,drivers,relays in [('AB','PS1',['U3','U4'],['K2','K3']),('CD','PS2',['U5','U6'],['K4','K5'])]:
        positive={(ps,'+V1'),(ps,'+V2'),*((d,'1.+') for d in drivers),*((r,'COM') for r in relays)}
        negative={(ps,'-V1'),(ps,'-V2'),*((d,'1.-') for d in drivers),*((d,'4.5') for d in drivers)}
        assert same(*positive)==positive
        assert same(*negative)==negative
        groups[group]=(owner[(ps,'+V1')],owner[(ps,'-V1')])
    assert len({*groups['AB'],*groups['CD'],'GND','+3V3'})==6
    motor={'A':('3.11','3.9','4.15'),'B':('4.6','4.12','4.14'),'C':('4.9','4.7','3.6'),'D':('4.13','4.5','4.8')}
    for i,(axis,mp) in enumerate(motor.items()):
        d='U'+str(i+3);r='K'+str(i+2)
        exact(('U2',mp[0]),(d,'4.1'));exact(('U2',mp[1]),(d,'4.2'))
        exact(('U2',mp[2]),(r,'IN'));exact((r,'NO'),(d,'4.3'))
        assert {(d,'4.13'),(d,'4.14'),(r,'GND')} <= nets['GND']
        assert (r,'VCC') in nets['+3V3']
        assert owner[(d,'4.22')] not in {'GND',*groups['AB'],*groups['CD']}
    exact(('U1','4.1'),('U2','4.2'))
    exact(('U1','4.3'),('U2','4.3'),('SW1','1'))
    exact(('U2','3.12'),('K1','IN'));exact(('K1','NO'),('U1','4.6'))
    assert {('K1','VCC'),('K1','COM')} <= nets['+3V3']
    assert {('K1','GND'),('SW1','2')} <= nets['GND']
    for port,rs,rp,c,dl,dh in [('3.12','R1','R3','C1','D1','D3'),('3.11','R2','R4','C2','D2','D4'),('3.9','R5','R6','C5','D5','D6'),('3.8','R7','R8','C6','D7','D8')]:
        exact(('U1',port),(rs,'2'),(rp,'1'),(c,'1'),(dl,'1'),(dh,'2'))
        assert {(rp,'2'),(c,'2'),(dl,'2')} <= nets['GND']
        assert (dh,'1') in nets['+3V3']
    definitions={x.attrib['part']:x for x in tree.findall('./libparts/libpart')}
    assert {p.attrib['num'] for p in definitions['AZD_K'].findall('./pins/pin')} == {*(f'4.{i}' for i in range(1,25)),*(f'1.{x}' for x in ['MB1','MB2','+','-','FG'])}
    assert {p.attrib['num'] for p in definitions['VCT_RELAY_MODULE'].findall('./pins/pin')} == {'VCC','IN','GND','NC','COM','NO'}
    assert len(definitions['MDR_100_24'].findall('./pins/pin'))==9
    parts={c.attrib['ref']:c.find('libsource').attrib['part'] for c in tree.findall('./components/comp')}
    assert sum(p=='AZD_K' for p in parts.values())==4
    assert sum(p=='MDR_100_24' for p in parts.values())==2
    assert sum(p=='VCT_RELAY_MODULE' for p in parts.values())==5
    print('PASS: four drives, five relay modules, two supplies; IN-COM5 returns; three separate grounds; all GPIO/ADC/UART/reset nets.')

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Usage: python verify_connections.py exported.net.xml')
    verify(sys.argv[1])
