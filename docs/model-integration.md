# M1/T7 実行構造から関数内の再構成・探索へ

T7（#9）は、T4/T5/T6の静的中間グラフをT1の`execution_structure`へ変換し、
スレッド役割、論理Queue、callback、呼出しの参照からT3の関数内CFGを選択する。
並行構造と非同期受渡しは型付き接続として残し、関数内CFGの辺に追加しない。
画面の展開はT8、固定版OAIの実抽出・総合検証はT9（#11）が担当する。

## 作成と選択

```bash
python3 -m onion.integration --manifest out/manifest.json \
  --extraction out/extraction/extraction.json --target nr-softmodem --out out/integration
```

`workspace.json`は共通構造、元の全中間グラフ、関数と元抽出物の対応、
`references`（選択元・対象関数・接続ID・元レコード）、失敗・frontierを保持する。
`structure.json`はT1契約で検証可能な静的構造である。
入力manifestと抽出artifactは既存T4の整合検証を通す。
1 workspaceは1 executable target。共有TUもtargetごとのbuild/function IDに分離する。
T1のパスはファイルシステムルートを基準とする相対POSIXパスであり、元の物理rangeは中間グラフに残る。
構成のmanifest ID、コンパイラ・引数・依存hashが識別子に含まれる。

```python
from onion.integration import (connect, references_from, open_region, initial_plan,
    reconstruct_region, recommend_region, accumulate_regions)
from onion.build_inputs import digest

workspace = connect(manifest, extraction, 'nr-softmodem')
choices = references_from(workspace, thread_role_id)  # Queue/callback/relation IDも可
region = open_region(workspace, choices[0]['id'], probe_catalog)
plan = initial_plan(region)
# producerが正しい区間・planで記録したことを確認したtraceのみ渡す。
# trace headerにはstructure_id/build_id/function_id/region_id/plan_id、
# plan_digest=digest(plan)、run_id、および既存Coreの観測契約が必要。
result = reconstruct_region(region, plan, trace)
next_plan = recommend_region(region, plan, result, max_new_sites=1)
# next_planは次のrunで使用する。過去runへ適用してはいけない。
evidence = accumulate_regions([result, next_result])
```

`references`はスレッド開始から同期呼出しの子関数へも移動できる。
Queueは投入・取得・処理候補の元range、role、call context、公開・取得の所有権契約を保持する。
Queueリンク、応答先の書換え、NULLや未知callbackの代替は元グラフから参照できる。
T1ではQueue投入/取得、callback呼出し/束縛を表現し、転送や処理候補の細部はnavigationの
`source_record`とT6グラフに保持する。静的構造から観測や動的インスタンスを生成しない。

## 区間と不完全性

区間は一つの関数の二つの明示的anchorで囲む。初期値は`work.begin`/`work.end`。
必要なprobeはT3同様、DeclRefExpr symbolが一箇所へ対応するものを明示的に供給する。
OAIにプローブを自動挿入する機能はない。
同じCFG block内の二anchorは、文単位の切出しがないため拒否する。
開始blockではanchorより前、終了blockではanchorより後のprobeを`expression_index`で拒否する。
呼出しや文単位の不完全性は、同じAST rangeを持つCFG式の評価順で区間内外を判定する。
位置を特定できないものや関数全体の不完全性は保守的に保持する。
到達不能な終端や区間外probeも拒否する。終端に到達しない分岐や循環は削除して一意化せず、
既存Coreの列挙不完全性に残す。

`open_region(..., anchors=('inner.begin', 'inner.end'), parent=parent)`で子区間を作れる。
同一workspace・参照contextの厳密に狭い境界で、親の終了で切断済みのCFGに対する
node・辺の包含と、境界block内の式位置を検証する。親の終了を越えて次の反復へ進む子区間は拒否する。
親ID・親の不完全性を保持し、親区間と以前のplan/候補/runを変更しない。
区間IDはanchor、probe対応、親、モデル、contextを含むcontent digestを持つ。
block IDにはT1 function IDを付け、別build/targetの蓄積を混同しない。

対象buildの抽出失敗、選択contextの未解決接続、区間内の未解決呼出し、
関連する探索frontierをCoreの`uncertainties`に渡す。
無関係の関数の診断はworkspaceへ保存し、選択区間の一意性を一律に妨げない。
候補Queue受渡しやcallback実行をまたぐ場合は、ローカル候補が一つでも`incomplete`となり、
certaintyに基づく追加probe選択を停止する。
静的接続の成立条件はregionに残るが、満たされたと仮定して実行済みとは判定しない。
`unique`は選択された関数内モデルと観測契約に限る。外側のスレッド生成、Queue移動、
実行context自体の成立を証明しない（`runtime_context_proven: false`）。

## plan/runと出典

- planとtraceは完全な区間scopeを照合する。traceはplanの内容digestも照合する。
- raw eventにbuild/plan/run/region等があればheaderと一致しなければ拒否する。
- 欠落・unknown site・anchor欠落・thread内連続性は既存Coreが検証する。
- 再構成はplanのsnapshot、raw trace digest、接続の出典、候補と観測の意味を保持する。
- `accumulate_regions`は同じ区間に限り、以前の再構成全体をrun別に保持する。
  重複run、別build/別区間の無対応な混合は拒否する。別runの候補を確定履歴へ変更しない。
- workspace/region/reconstructionのdigestは偶発的な変更を検知するためのもので、
  信頼できないproducerに対する認証ではない。観測契約と正しい計測はproducerの責務。

## 検証

`fixture/model-integration`はスレッド開始、Queue投入/取得、callback、直接処理関数、
外側/内側anchorと分岐probeを持つ小規模C fixture。
Queueとthread APIの実装には抽出用stubを用い、観測はテストで明示した合成runである。
OAIのruntimeイベントや実スレッドの観測と混同しない。

`test_model_integration.py`はnative依存なしの区間・plan・run・欠落・候補保持テスト。
`test_model_native.py`はClang 18実抽出からT1、スレッド/Queue選択、段階的探索、
子区間、複数targetの分離と既存Queue fixtureを検証する。native tool不足はskipせず失敗する。
通常CIはworkspace、structure、manifest、元抽出物とテスト結果を`out/integration`等へ保存する。
従来の再構成、欠落、run分離のテストも同時に実行する。
