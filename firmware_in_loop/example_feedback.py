"""循環モデルとの接続例。制御則は元アプリ/元C++内にあります。"""
def settings():
    from feedback_model import defaults
    result=defaults();divider=47000/(47000+510)
    for a,name,unit,gain in zip('abcd',('大動脈圧','左冠流量','右冠流量','左室圧'),('mmHg','mL/min','mL/min','mmHg'),(100,200,100,100)):
        result['channels'][a].update(name=name,unit=unit,gain=gain/divider,window_ms=1200)
    for i,axis,current,target in ((0,'C','b',100),(1,'D','c',60)):
        result['loops'][i].update(enabled=True,axis=axis,current=current,formula='a-d',table=[[0,target],[200,target]],initial_mm=4)
    result['loops'][2]['enabled']=False
    return result
