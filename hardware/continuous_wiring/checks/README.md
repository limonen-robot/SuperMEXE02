# 連続配線図の確認記録

2026-11-14、KiCad 10.0.8。比較元は元図コミット `543c0be` の接続内容です。

- ERC：エラー0・警告0（同梱レポート）。
- シート1枚、グローバルラベル0、同名のローカルラベルの重複0。
- `verify_continuity.py`：ラベルと電源名を無視し、線の端点・接続点・部品ピンを解析。299本の直交線分だけによる全ネットの接続を確認。
- 元図と連続図のネットリストを、全ネットに属する部品ピンの集合で比較して完全一致。
- 既存の `../verify_connections.py` による4軸GPIO・ADC・IN-COM5・リレー・UART・共通リセット・電源分離の検査にも合格。
- PNG/SVGで配置・配線・注記を目視確認。
- 色分け更新：接続済み34ネットを9種類のネットクラスに割り当て。SVGで全種類の指定色を確認し、従来の緑のまま残る配線がないことを確認。
- VCT・ZHOME接点出力の6ネットには固有のローカル名を追加。ラベルの重複はなく、線だけによる接続・元図との一致・ERCを再確認。
- 色分けはプロジェクト設定と図中の凡例に保存。初回は連続図のみ、下記の配色統一では元図も更新。ファームウェア・PCアプリは変更なし。
- 連続図初版作成時には元図を変更せず保持。その後、利用者指定により両図へ共通の配色と凡例を適用（接続内容の変更なし）。

図面上の接続確認であり、実機の電圧・電流・ノイズ・絶縁・接点時間を検証したものではありません。

再確認（この連続配線図フォルダーで実行し、KiCadのbinをPATHへ追加）：

```powershell
kicad-cli sch erc --severity-all --exit-code-violations -o checks/continuous.erc.rpt SuperMEXE02_Continuous.kicad_sch
kicad-cli sch export netlist --format kicadxml -o continuous.net.xml SuperMEXE02_Continuous.kicad_sch
kicad-cli sch export netlist --format kicadxml -o original.net.xml ../SuperMEXE02.kicad_sch
python ../verify_connections.py continuous.net.xml
python verify_continuity.py SuperMEXE02_Continuous.kicad_sch continuous.net.xml original.net.xml
```

## 両図の配色統一（2026-11-14）

- 利用者指定：CWをオレンジ、CCWを青、センサーを緑に統一。ZHOMEはCWと区別するため赤紫へ変更。
- 元図・連続図それぞれの接続済み34ネットについて、KiCad出力のネットクラスが指定分類と一致することを確認。
- 両図の全ネットの部品ピン集合は、今回の変更前と完全一致。4軸GPIO・ADC・IN-COM5・UART・共通リセット・電源分離の既存検査に合格。
- 連続図は299本の線分だけによる接続検査にも合格。両図のERCはエラー0・警告0。
- SVGで全9色の配線出力を確認し、PNGで凡例と全体配置を目視確認。ファームウェア・PCアプリは変更なし。
