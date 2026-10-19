# 実行マニュアル

M1の導入・実行・結果確認の手順です。機能と対象外は[README](../README.md)、
完了時の条件と証拠は[M1受入結果](m1-results.md)、判定の仕様は[固定OAI総合受入](oai-acceptance.md)を参照してください。
以下のコマンドはLinuxのbashで、リポジトリのルートから実行します。

## 1. 目的に合う入口を選ぶ

| 目的 | 手順 |
| --- | --- |
| 保存済みの画面を見る | このページの「成果物だけを閲覧する」 |
| OAIなしで動作を一巡確認する | 「環境を用意する」→「fixtureを実行する」→「画面を操作する」 |
| 固定OAIを実ビルドして受入を再現する | 「固定OAIの総合受入を実行する」 |
| JSONや抽出条件を調べる | 「成果物と保存」から詳細仕様へ進む |

## 2. 環境を用意する

fixtureはLinux、Python 3.10以降、Clang/LLVM 18、CMake 3.20以降、Ninja、C/C++ビルド環境を使います。
検証基準はUbuntu 24.04です。固定OAIの再現は同OSのx86_64環境とPython 3.12を基準とします。
別OS・CPUでの同等動作は検証していません。Python側の実行に追加のpipパッケージは不要です。

```bash
sudo apt-get update
sudo apt-get install -y python3 git build-essential clang-18 libclang-rt-18-dev \
  libclang-18-dev llvm-18-dev cmake ninja-build
git clone https://github.com/fregata-ariel/OnionTrace.git
cd OnionTrace
git switch main
git rev-parse HEAD
python3 --version
clang-18 --version
cmake --version
```

既存checkoutがある場合はそのルートへ移動してください。作業中の変更を上書きせず、実行したcommitを記録します。
Clang抽出器の版は18固定です。別配置のLLVMを使う場合は`LLVM_DIR`と`Clang_DIR`を対応するCMake設定ディレクトリへ指定します。
ブラウザの自動検証には別途Node.js 24/npmとChromiumが必要です。HTMLを閲覧するだけなら不要です。

## 3. fixtureを実行する

```bash
python3 scripts/ci.py --cc clang-18 --out out
```

pthread fixtureの3段階の実行、LibTooling抽出器のビルド、thread・callback・Queue・構造fixtureの抽出、
再構成・推薦・HTML生成、必須Pythonテストを行います。OAIの取得・ビルドは行いません。
終了値0を確認し、`out/test-summary.json`の`passed`がtrue、`failures`・`errors`・`skipped`が0であることを確認します。
M1完了時は200テストです。バージョン変更後は件数そのものより、そのrevisionのテスト結果を確認してください。

出力先は固定名を含み、再実行すると前の結果を置き換えます。結果を保存する場合は
`--out out/experiment-001`のようにディレクトリを分け、以降のパスも読み替えてください。

| ファイル | 内容 |
| --- | --- |
| `out/onion-demo.html` | 実pthread fixtureの計測・候補経路・追加地点推薦 |
| `out/incomplete-demo.html` | 欠落状態を人工的に与えた診断例。OAIの欠落実測ではない |
| `out/structure-only.html` | 小規模fixtureの両mainから構造とCFGを閲覧 |
| `out/structure-demo.html` | 構造とpthread時間軸を切替。別buildの証拠は関連付けない |
| `out/session.json`, `out/stage-*/` | 各runのplan・trace・analysis・next-planと統合session |
| `out/acceptance/acceptance.json` | fixtureの受入判定。合格でもOAIの`full_extraction_verified`はfalse |
| `out/test-summary.json`, `out/tests.log` | テスト集計と診断 |

pthreadデモだけを生成する場合は次も使えます。構造fixtureの一巡検証や必須全テストは含みません。

```bash
python3 -m onion.demo --cc clang-18 --out out/demo-only
```

### DOMとChromiumも確認する

Node.js 24/npmを用意し、上記のfixture生成後に実行します。

```bash
npm ci
npx playwright install --with-deps chromium
npm test
```

`npm test`は`out`を前提にDOM検証とChromiumテストを行います。M1完了時はChromium 16テストです。
独自の出力先を使った場合は、DOMとブラウザそれぞれに次のように指定します。

```bash
node tests/check_dom.cjs out/experiment-001/onion-demo.html out/experiment-001/qa-dom
ONION_OUTPUT="$PWD/out/experiment-001" npm run test:browser
```

`ONION_OUTPUT`だけでは`npm test`内のDOM入力先は変わりません。
失敗時はターミナルの診断、`test-results/`、`playwright-report/`を確認します。

## 4. 画面を操作する

生成HTMLをブラウザの「ファイルを開く」から開きます。CSS・JavaScript・データは埋込み済みで、サーバは不要です。

### 構造とCFG

1. `structure-only.html`を開き、targetから`nr-softmodem`または`nr-uesoftmodem`を選びます。
2. mainの枝を開き、thread、Queue、callbackの関係をたどります。大きな枝は前／次ページで16接続ずつ移動します。
3. 関係を選び、条件・ソース位置・hash・抽出元・未解決理由を確認します。
4. 「CFGを開く」で対象関数へ移動し、blockを選択して式・successor・不完全性を確認します。
   CFGは60blockずつ追加表示できます。要素上限に達した場合は他の枝を折り畳みます。
5. targetや枝を切り替え、戻った際の関数・block選択を確認します。

`resolved`は静的接続、`candidate`は候補、`unresolved`は未解決です。どれも実行済みの証拠ではありません。
固定OAIのHTMLは監査対象関数のCFGだけを含み、`display_scope`に表示数と対象外を記録します。
詳細は[構造ビューア](structure-viewer.md)を参照してください。

### pthreadの再構成

1. `onion-demo.html`でstageとjobを選び、対応するrun・plan・候補経路・推薦地点を確認します。
2. 再生位置を動かし、直接記録した地点と、完全な観測契約とモデルから推定した部分を確認します。
3. 次のstageへ進み、以前のrunの候補が、そのrun自身の結果として残ることを確認します。

青は直接観測、黄はモデル内推定です。再生位置より後のイベントは推定に使いません。
後のrunの結果で前のrunを確定しません。表示された区間時間は観測位置間の時間で、純粋なQueue滞留時間ではありません。
`structure-demo.html`では構造とこの時間軸を切り替えられますが、OAIの実行記録を生成する機能ではありません。

## 5. 固定OAIの総合受入を実行する

両softmodemをSIMUで実ビルドし、固定10翻訳単位の静的構造・CFG・実ブラウザ表示を検証します。
softmodem本体は実行しません。USRPやRFドライバは不要です。
ネットワークと約14GB以上の空き容量が必要です。CIの上限は全体180分、意味判定20分、ブラウザ8分です。
監査範囲とCPU・依存条件は[lock設定](../config/oai-2026.w07.json)と[解析範囲](../config/oai-analysis-scope.json)が正本です。

### GitHub Actionsで実行する

1. リポジトリの **Actions → OAI pinned acceptance → Run workflow** を開きます。
2. 対象ref（M1統合後は`main`）を選び、実行します。実行権限が必要です。
3. build、capture、extract、acceptance、browserの各stepが成功したことを確認します。
4. 完了runのArtifactsから`oai-2026-w07-<run_id>-<attempt>`を取得します。
5. 後述の判定JSONと`oai/acceptance/structure.html`を確認します。

通常のfixture CIはOAIを取得しません。OAI workflow自体・driver・lock等の指定ファイルを変更するPRでも
独立workflowが動きますが、README等だけの変更では自動起動しません。
`oai-native-*`は意味判定より前の証拠保存用であり、総合受入完了を示す成果物ではありません。

### ローカル環境を準備する

Ubuntu 24.04の専用環境を用意し、初回導入時に実行します。以下はworkflowのOS依存と同じ構成です。

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3 git clang-18 libclang-18-dev \
  llvm-18-dev libclang-rt-18-dev cmake ninja-build build-essential automake autoconf \
  libtool pkg-config bison flex libblas-dev liblapack-dev liblapacke-dev \
  libreadline-dev libconfig-dev libsctp-dev libssl-dev zlib1g-dev xxd \
  libyaml-cpp-dev fonts-noto-cjk
cmake -S tools/libtooling -B build-libtooling -G Ninja \
  -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm \
  -DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang
cmake --build build-libtooling --parallel 2
```

ASN.1/SIMDeは**まだ存在しないprefix**へ導入します。再実行で既存prefixを上書きしません。

```bash
python3 scripts/install_oai_dependencies.py --prefix out/oai-deps-001 --jobs 2
export ONION_ASN1C="$PWD/out/oai-deps-001/asn1c"
export ONION_SIMDE="$PWD/out/oai-deps-001/simde"
export PATH="$ONION_ASN1C/bin:$PATH"
export C_INCLUDE_PATH="$ONION_SIMDE/include${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}"
export CPLUS_INCLUDE_PATH="$ONION_SIMDE/include${CPLUS_INCLUDE_PATH:+:$CPLUS_INCLUDE_PATH}"
```

`out/oai-deps-001/environment.json`に記録した値と一致する環境で、以降の全段階を実行します。
導入scriptはローカルの親shellを変更しません。Actionsでは`GITHUB_ENV`経由で次のstepへ渡します。
Node.js 24/npmを用意し、ブラウザ依存も導入します。

```bash
npm ci
npx playwright install --with-deps chromium
```

### capture → 意味判定 → ブラウザの順に実行する

`out/oai-001`は**まだ存在しないパス**にしてください。既存の運用checkoutやbuildは再利用しません。

```bash
python3 scripts/oai_ci.py --out out/oai-001 \
  --tool build-libtooling/onion-extract --jobs 2 --capture-only
```

ここまでの終了値0は、取得・ビルド・入力検証・native抽出までの成功です。総合受入は未完了です。
同じ出力先に対し、保存した入力と抽出のhashを再照合して意味判定を進めます。
CIと同じく、意味判定の仮想メモリ上限を6GiBにします。括弧内だけに適用します。

```bash
(
  ulimit -v 6291456
  python3 scripts/oai_ci.py --out out/oai-001 --resume-acceptance --require-browser
)
node scripts/check_oai_browser.cjs out/oai-001
```

意味判定終了時はブラウザ待ちのため、`run.json.passed`はまだfalseです。
最後のChromium検証が成功してから総合成功になります。各コマンドの終了値を確認して次へ進んでください。
`--capture-only`も`--resume-acceptance`も付けない実行はブラウザなしの意味判定で終了するため、
その`passed`だけではブラウザを含む総合受入の証拠になりません。

入力・依存・抽出器を変更した場合は、新しい出力先でcaptureからやり直します。
resumeは直前のcaptureの継続専用です。取得・準備だけの`--fetch-only`やsource auditも総合受入とは異なります。

### 成功を確認する

```bash
python3 - <<'PY'
import json
from pathlib import Path
root = Path('out/oai-001')
run = json.loads((root / 'run.json').read_text())
acceptance = json.loads((root / 'acceptance/acceptance.json').read_text())
browser = json.loads((root / 'browser/summary.json').read_text())
checks = {
    'run': run.get('passed') is True,
    'semantic': run.get('semantic_acceptance_verified') is True,
    'browser': run.get('browser_verified') is True,
    'full_extraction': run.get('full_extraction_verified') is True,
    'acceptance': acceptance.get('passed') is True
        and acceptance.get('full_extraction_verified') is True,
    'browser_result': browser.get('passed') is True,
}
print(json.dumps(checks, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
PY
```

すべてtrueであることを確認し、`acceptance.json`の6判定と`browser/summary.json`の5操作・診断を確認します。
`acceptance/structure.html`から両main、選択thread・Queue、完了通知の取得側のCFGを開けます。
`full_extraction_verified`も指定した監査範囲の成功を意味し、全OAIの解析完了や実行済みを意味しません。

## 6. 成果物と保存

| 入口 | 生成元・用途 |
| --- | --- |
| `out/build-inputs/` | 通常CIのコンパイル条件fixtureのmanifest |
| `out/source-extraction/`, `out/threads/`, `out/callbacks/`, `out/queues/` | 通常CIの抽出・中間グラフ・診断 |
| `out/integration/` | 再構成接続用fixtureのworkspace・構造 |
| `out/structure-viewer/` | 両targetの構造fixtureのmanifest・抽出・workspace |
| `out/oai-001/run.json`, `failures.json`, `*.log` | OAIの段階別成否、ref・固定条件・失敗理由 |
| `out/oai-001/inputs/` | 全compile DBの対象／除外／失敗、入力hash |
| `out/oai-001/extraction/` | native CFG・呼出しとログ、全抽出結果 |
| `out/oai-001/acceptance/` | 個別判定、各targetのworkspace・構造・表示projection、HTML |
| `out/oai-001/browser/` | 実Chromium結果・版と5画面 |

fixtureのworkspaceからHTMLだけを再生成する例です。`--session`を省くと静的構造だけになります。

```bash
python3 -m onion.viewer \
  --workspace out/structure-viewer/nr-softmodem.json \
  --workspace out/structure-viewer/nr-uesoftmodem.json \
  --session out/session.json --out out/structure-regenerated.html
```

固定OAIは大きいため、既存の監査対象projectionを含む`acceptance/structure.html`を利用してください。
workspace全体をこのfixture用コマンドに渡して同じサイズのHTMLになるとは限りません。
manifest・抽出物は絶対パスや実ファイルhashを含み、別の場所へ移しただけでは再解析できません。
HTMLは単体で閲覧できます。再解析する場合は、その環境でcaptureから生成してください。

### 成果物だけを閲覧する

Actionsの対象runでArtifactsをダウンロードし、ZIPを展開してHTMLを開きます。
通常CIの`viewer-evidence`等には`out/`、OAIの全成果物には`oai/`を含む配置があります。
アーカイブ内で`onion-demo.html`／`structure-only.html`／`acceptance/structure.html`を探してください。
GUIのないビルド環境からHTMLだけをPCへコピーしても閲覧できます。

Artifactsの保存期間は14日です。残したい結果は期限内に取得し、実行ref・commit、run URL、
manifest、判定JSON、ログ、HTML、必要なnative抽出とともに保管してください。
M1完了時のrun・digestは[M1受入結果](m1-results.md)に記録しています。

## 7. 失敗したとき

| 症状 | 確認するもの・対処 |
| --- | --- |
| Clang／LLVM設定が見つからない、nativeテストが失敗 | Clang 18、`libclang-18-dev`／`llvm-18-dev`、LLVM_DIR／Clang_DIRの対応を確認。missing toolをskipして成功扱いしない |
| HTMLがない／ブラウザテストが入力を読めない | 先にfixture生成を完了し、`--out`とDOMパス・ONION_OUTPUTを合わせる |
| Chromium起動・共有ライブラリ不足 | `npx playwright install --with-deps chromium`を実行し、対象Node/npm環境をそろえる |
| OAI依存準備が失敗しrun.jsonがない | `out/oai-deps-001/*/install.log`と端末／Actions stepログ。まだOAI runner開始前の場合がある |
| prefix／出力先が既に存在する | 残した結果を確認し、新しいprefix・出力先を指定する |
| OAIのbuildが失敗 | `run.json`、`build.log`／`build-error.log`、依存環境とlockのCPU条件を確認。成功メッセージだけで判断しない |
| ヘッダ不足・未対応引数・入力不整合 | `inputs/failures.json`。生成物とコンパイル条件を修復し、新しいcaptureを作る |
| native抽出が失敗 | `extraction/failures.json`と各`entry-*.log`。manifest作成後の入力変更も確認 |
| 意味判定が失敗・resumeが拒否される | `acceptance/failures.json`と`run.json`。入力・抽出hashや段階不一致を調べ、新しいcaptureから再実行する |
| HTMLは開くがOAI全体が未合格 | `run.json`のsemantic/browserフラグ、6受入判定、`browser/summary.json`のfailure・errors・requestsを確認 |
| 枝・CFGが途中までしか見えない | 前／次ページ、追加block表示、他の枝の折り畳み、`display_scope`と未解決理由を確認 |

コンパイル引数を削除したり、失敗一覧を除外したりして合格にしないでください。
詳細な低水準CLI・形式は[ドキュメント一覧](README.md)から各仕様へ進めます。
