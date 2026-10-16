# M1/T2: OAI解析入力

T2 (#4、親 #12) は、後続T3が読む入力と由来の境界を固定する。
`validated` は実際のコンパイル条件で構文・依存検査を通過した意味であり、CFG抽出や
OAI実行の成功を意味しない。固定版OAIでの総合受入は #11 で扱う。

## 固定条件と準備

[config/oai-2026.w07.json](../config/oai-2026.w07.json) を唯一の取得条件とする。
取得元は `duranta-project/openairinterface5g`、タグ `2026.w07`、commit は
`7fa4d018d4178f9664ea1ca377eca2e822c7a979`。タグの参照先とHEAD、originのURL、
追跡対象と未追跡ソースの変更を検査し、不一致を拒否する。

基準はLinux、Clang/Clang++ 18、CMake File API対応のCMake 3.20以降、Ninja。
Ubuntu 24.04の依存入り専用環境で行う。Clang 18だけでなく実際のCMake版と実行体のハッシュも
manifestに記録する。CMake版・OS依存の更新は入力条件の変更として扱う。

固定版の [build_oai](https://github.com/duranta-project/openairinterface5g/blob/7fa4d018d4178f9664ea1ca377eca2e822c7a979/cmake_targets/build_oai) と
[build_helper](https://github.com/duranta-project/openairinterface5g/blob/7fa4d018d4178f9664ea1ca377eca2e822c7a979/cmake_targets/tools/build_helper)
に従う。`--gNB --nrUE -w SIMU --ninja` を使い、両コンパイラ、
`CMAKE_EXPORT_COMPILE_COMMANDS=ON`、`CMAKE_BUILD_TYPE=RelWithDebInfo` を明示する。
compiler launcherを介さないよう`CCACHE_ACTIVE=OFF`とし、CPM_SOURCE_CACHEもworkspace内へ分離する。
`--gNB`はnr-cuupも、SIMUと共通処理はrfsimulator等もビルドするが、解析対象は両softmodemの
CMake依存閉包とする。dlopenで読み込むライブラリを自動的に依存閉包へ追加しない。

既存の運用checkout・ran_buildを使わず、**まだ存在しないディレクトリ**を渡す。
準備スクリプトは既存ディレクトリを上書きせず、root権限のインストールやcleanを実行しない。
ネットワークとビルド依存は事前に用意する。

```bash
# 取得と固定条件の確認だけ。後続の本ビルドとは別ディレクトリにする。
python3 scripts/prepare_oai.py --workspace /absolute/oai-fetch-check --fetch-only

# 依存入りの専用環境で、新しい解析checkoutを作成してbuild_oai→manifestまで実行。
python3 scripts/prepare_oai.py --workspace /absolute/oai-analysis-001 \
  --dependency-lock /absolute/dependencies.json
```

ソースは`workspace/source`、buildはその中の`cmake_targets/oniontrace-analysis/build`。
File API queryを**configure前**に置き、build_oaiの`-d oniontrace-analysis`を使う。
`preparation.json`に取得条件、実行引数、cwdを保存し、`build.log`にconfigure/buildの出力を保存する。
失敗時はその場で停止する。`--fetch-only`は取得検証のみでありビルド済みとは扱わない。

依存のインストールは固定版build_helperの`check_install_oai_software`、
`install_asn1c_from_source`、`install_simde_from_source`の内容を環境構築者が確認して専用環境で行う。
自動インストールの実行履歴、OSイメージのdigest、パッケージアーカイブは環境構築側で保存する。
build_helperが外部ソースを取得するため、OAIタグだけでは依存を固定できない。
OSパッケージはdpkg-queryの実名・版・architecture一覧とそのSHA-256を自動記録する。
OSパッケージ外のASN.1コンパイラ、SIMDe、CPM等の取得物は、ビルドで実際に使う版とパスを
次のJSON配列で列挙する（例の版文字列を実測したcommit/tag/versionに置換する）。

```json
[
  {"name": "asn1c", "version": "940dd5fa9f3917913fd487b13dfddfacd0ded06e", "path": "/opt/asn1c"},
  {"name": "simde", "version": "c7f26b73ba8e874b95c2cec2b497826ad2188f68", "path": "/usr/include/simde"}
]
```

`version`は環境構築者が指定したラベル。collectorがそのラベルの真偽を保証するものではない。
上記2件の版は固定版build_helperの既定値。SIMDE_VERSIONを変更した場合は実際の版を記録する。
全ファイルのSHA-256を別途計算するため、同じラベルのローカル変更もキャッシュを無効にする。
CMakeが追加取得した外部ソースも同様に列挙する。空の依存一覧、欠落パス、空ディレクトリは拒否する。
生成ヘッダは依存スキャンで実際に読まれたものを記録し、生成ツールの版・実体はこの一覧で補う。
パッケージ一覧・入力ハッシュは追跡情報であり、パッケージアーカイブやコンテナ自体の代替ではない。

## 入力と出力

準備後に再captureする場合（checkoutはclean、SIMUは実キャッシュでもONであること）：

```bash
python3 -m onion.build_inputs capture \
  --build /absolute/oai-analysis-001/source/cmake_targets/oniontrace-analysis/build \
  --target nr-softmodem --target nr-uesoftmodem \
  --dependency-lock /absolute/dependencies.json --out /absolute/capture-001
```

File APIが無い場合は`python3 -m onion.build_inputs query --build BUILD`を実行してから
同じ条件でCMakeを再configureする。multi-config generatorは未対応として拒否する。
構成ごとに独立したsingle-config buildを作成し、必要なら`--configuration`で構成名を照合する。
JSON、cwd、ソース所属またはターゲットが不整合なら成功扱いしない。

| 出力 | 意味 |
| --- | --- |
| `manifest.json` | 版、入力ハッシュ、CMake由来、全compile DBエントリ、集計、ready |
| `targets.json` | 選択対象のうち構文・依存検査を通過したコンパイル条件 |
| `excluded.json` | 選択ターゲットの依存閉包に属さないエントリと理由 |
| `failures.json` | 構文エラー、生成物不足、未対応条件、入力不整合と理由・診断 |

エントリはcompile DBの順序・原文・引数配列・cwdを保持する。同一ソースが複数ターゲット・
複数条件でコンパイルされる場合もまとめない。出力オブジェクトのターゲットディレクトリと
File APIのソース所属・compileGroupIndexを照合する。File APIの対象TUがcompile DBに無い場合も失敗。
不明な出力形式、compiler launcher、response file、プラグイン、C/C++以外の言語は黙って通さない。
現実装の対応範囲はLinuxのCMake Makefiles/Ninjaが出力する`CMakeFiles/<target>.dir`形式。
別形式・unity build等で照合できない場合は失敗理由を出力する。
両profileとも直接起動するClangのC/C++を対象とし、genericはOAIの固定版・Clang 18の版指定を外す。
compile DBの実行体とCMakeCacheの言語別コンパイラを実パスで照合し、launcherや不一致は
genericでも理由付き`failed`とする。他のcompiler familyは主入力を検証できないため未対応とする。

出力・depfile用の`-c/-o/-MD/-MMD/-MP/-MF/-MT/-MQ/-MJ`のみを検査用に差し替え、
その他の意味を持つ引数は保持する。`-fsyntax-only -MD`でsystem headerを含む依存を取得する。
同時にClangの`-MJ`で実際の主入力・cwd・driverを取得し、主入力が1件だけで、
compile DBの`file`・実行cwd・記録したコンパイラと一致することを検査する。
主入力そのものが依存一覧に含まれ、その内容のSHA-256を記録できた場合だけ`validated`とする。
`validation.main_input`と`validation.compiler_path`に照合結果を保存し、cache_keyにも含める。
別ソース、複数主入力、主入力・driverの照合不能、主入力ハッシュの欠落は`failed`でcache_keyを持たない。
宣言されたソースを単に`-include`で依存へ加えても、主入力の一致とは扱わない。
Clangの未知のwarning optionと未使用引数はerrorへ昇格させる。
生成ヘッダ不足・未知の引数・compiler不在・timeoutは`failed`となり、cache_keyを持たない。
`ready`は失敗0件かつ検査通過1件以上の場合だけtrue。captureは失敗時も一覧を書き、終了値1を返す。
configure/File API自体の不備はトップレベルの失敗として記録する。

## キャッシュ境界

cache_keyは将来の抽出器へ渡す入力識別子であり、T2はキャッシュ済みの抽出結果を再利用しない。
毎回検査して依存を再列挙するため、新たにincludeされたファイルも反映する。
キーにはsource・cwd・順序付き引数、実コンパイラの版・実体ハッシュ、全依存ファイルのハッシュ、
ターゲット・compile group、File API reply、CMakeCache、compile DB、CMake configure入力、
collector実体、明示した外部依存、OSパッケージ一覧、関連環境変数、platformを含む。
絶対パスを保持し、別配置のcheckout間での同一性は保証しない。設定の変更は保守的に全TUを無効化する。
後続の抽出結果を保存するときは、このcache_keyに**抽出器の版とオプション**も加える。
T1のcompilationの`input_digest`等への接続はT3でこの境界を用いて行う。

manifestにはローカルのパスやパッケージ情報が含まれる。検査中に入力を変更しないこと。
入力は信頼できる自分のビルドディレクトリを使う（compile DBに指定されたコンパイラを実行する）。

## 通常CI

`fixture/compilation`はOAIを取得しない小規模プロジェクト。
両ターゲット、共有STATIC/OBJECTライブラリ、同一ソースの異なるdefine、除外ライブラリ、
configure生成ヘッダを含む。`tests/test_build_inputs.py`を必須テストに含め、Clang 18で
対象選択、条件・生成物・外部依存変更、欠落TU、生成ヘッダ不足、未知の引数とCLI終了値を検証する。
実際の主入力との差し替え、宣言側ソース変更、強制include、複数/主入力なし、照合情報/ハッシュ不足、
genericの`env clang ...`拒否も回帰検証する。
CIの`out/build-inputs`には実際のfixtureのmanifestと対象・除外・失敗一覧も保存する。
