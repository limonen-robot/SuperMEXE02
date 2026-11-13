# 回路図の確認記録

2026-11-13、KiCad 10.0.8で確認。

- 回路図読込み・SVG出力・XMLネットリスト出力：成功。
- ERC：エラー0、警告0。詳細は同梱レポート参照。
- XMLネットリスト照合：4つのADCネットが独立し、各ネットに対象GPIO・510 Ω抵抗出力・0.1 µF上端・47 kΩ上端・上下ダイオードの正しい極が接続されていることを確認。
- 各sensor OUTは対応する510 Ωの入力側だけへ接続。下側ダイオードのアノードはGND、上側ダイオードのカソードは3V3。
- C3/C4の電源接続と、論理VCTネット（motor PA0→sensor PB0）も確認。
- Rev 0.2：G431KBまでの直接配線、共通3V3/GNDラインへ配置変更。変更前後の全11ネットについて、全ての部品ピンの接続先が完全一致することを確認。
- Rev 0.3：左右15本ずつ全30端子の重複・欠落なし。公式UM2397 Figure 12/Table 11に対応させ、既存の論理GPIO名から物理ヘッダー端子へ変換後、全11ネットの接続先を照合して一致。GNDはCN3-2とCN4-4の両方へ接続。
- PNGで文字・部品・配線の配置を目視確認。

ERCは回路図上の接続検査であり、実配線の一致、ADC保護性能、基板の端子割当、センサー適合性を検証するものではない。M1は別ボードの論理出力を表し、実際のVCT配線は未確認。

再確認例（このhardwareフォルダーで実行。KiCadのbinをPATHに追加）：

```powershell
kicad-cli sch erc --severity-all -o checks/sensor_frontend.erc.rpt SuperMEXE02.kicad_sch
kicad-cli sch export svg -o previews SuperMEXE02.kicad_sch
kicad-cli sch export netlist --format kicadxml -o sensor_frontend.net.xml SuperMEXE02.kicad_sch
```
