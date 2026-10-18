# M1/T8 構造から関数内CFGを開く画面

T7の`execution_workspace`をそのまま入力に取り、main → 初期化呼出し →
スレッド役割、論理Queue、callbackを一段ずつ展開する。
「CFGを開く」で開始関数・Queue両端・処理候補・callback先の関数内CFGを選ぶ。
非同期の受渡しをCFG辺に追加しない。プローブがない関数も静的CFGとして閲覧できる。

```bash
python3 -m onion.viewer \
  --workspace out/gnb/workspace.json --workspace out/ue/workspace.json \
  --session out/session.json --out out/structure.html
```

`--workspace`は繰り返し指定可能。各targetのworkspaceはT7 CLIで作る。
`--session`は従来のpthread fixture時間軸を同じHTMLへ含める任意指定で、
省略すれば構造だけのHTMLになる。生成物はCSS・JavaScript・データを含み、
`file://`で開ける。ネットワーク、外部ライブラリ、サーバーは不要。

## 選択と証拠の意味

- 構造とCFGでentity・connection・reference・function・blockのIDを保持する。
  折り畳み・target切替後も元のCFGと選択blockを復元する。
- `resolved`はソース上の静的接続であり、実行確認ではない。
  `candidate`は静的候補、`unresolved`は未知端点として表示する。
  NULL callbackも登録地点と未解決のbindを残す。
- inspectorには関係の成立条件、locationのpath・byte offset・content digest、
  抽出方法、元レコード、CFG式・successor・不完全性とfrontierを保持する。
  callbackの登録地点は表示用attachmentとして明示し、T1の呼出しへ変更しない。
- 静的workspaceには観測・区間時間を生成しない。従来のFixtureでは青が
  このrunで直接記録した地点、黄が完全な証跡とモデルからの推定である。
  再生途中・欠落時の推定抑止も既存契約どおり。
- Fixtureの時間軸へ戻る際はrun・job・cursor・blockを保持する。
  実行骨格の観測地点・時間軸イベントを選ぶと同じjob・再生位置・対応CFG地点へ移動し、
  CFG blockを選ぶと対応する、この時点までに観測済みの時間軸地点を強調する。
  別buildのOAI静的構造にFixtureのイベントを関連付けない。
  OAI本体の実行イベント取込と実行時間表示は今回の範囲外。

## 大きな構造と検証

初期表示はmainのみ。一枝は16接続ずつ、深さ24、表示要素240までとし、
循環参照はその枝で再展開を止める。折り畳むと他の枝を開ける。
CFGは選択した一関数を60blockずつ表示し、残数と追加表示ボタンを出す。
表示されていない接続・block・未解決successorはデータから削除しない。

`fixture/structure-viewer`は二つのtargetを持つ小規模ソースfixture。
両main、条件付きthreadCreate、notifiedFIFO投入・取得・応答先、
callback・NULL・未知の関数ポインタをClang 18で抽出する。
OAIの実行記録ではなく、thread APIは抽出用stubである。
通常CIの`out/structure-demo.html`は従来Fixtureの時間軸付き、
`out/structure-only.html`は構造のみ。元workspace・manifest・抽出物も保存する。
ブラウザテストは両mainからthread/Queue/CFG、callbackと未解決表示、
選択復元、キーボード操作、390px幅、オフラインを検証する。
固定OAI両targetの実ビルド抽出と総合受入はT9（#11）で行う。
