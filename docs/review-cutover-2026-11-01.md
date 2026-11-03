# 旧版からのAPI・配布切替レビュー

確認日: **2026-11-01 UTC**。比較元はAPI`66d7d18`、webapp`547e928`です。以下はリポジトリ差分から判明した移行条件であり、実AWSのinventory・変更セット・staging切替を検証した記録ではありません。本レビューでは実AWS変更を行っていません。

## API IDとstageはそのまま維持されない

旧APIのSAM templateは`Type: Api`のイベントに`RestApiId`を指定せず、SAMのimplicit `ServerlessRestApi`を使います。暗黙APIのstageは`Prod`です。旧ファイルにある明示`HarvestApi`の定義はコメントです。

現行templateは明示的な`AWS::Serverless::Api`のlogical ID **`HarvestApi`** を作り、各eventがその`RestApiId`を参照します。`StageName`も **`EnvironmentName`** に変わり、template自体の既定値は`dev`です。したがって同じstackにそのまま更新すると旧APIの削除と別APIの作成を伴う可能性があり、旧execute-api ID・`/Prod` URLが利用できなくなり得ます。`EnvironmentName=prod`でもstage文字列は小文字`prod`であり、旧`Prod`とは異なります。

`deploy.sh`は`EnvironmentName=dev|staging|prod`の明示を必須にしました。stack名から環境を推測せず、未指定で`dev`へ流れることを防ぎます。ただしこの引数検証はAPI ID維持の対策ではありません。既に現行stackを導入している環境も考えられるため、このレビューでlogical IDを旧名へ戻す変更はしていません。

## 切替前に確認する順序

1. 対象account/region/stackと現在のAPI ID、stage、custom domainのbase path mapping、CloudFront origin、現行web runtime設定、Cognito pool/client、CORSを実inventoryで確認する。CloudFormation外で管理されるDynamoDB/S3/Cognitoも管理元を記録する。
2. **隔離したstaging資源**に新APIを構築し、旧新APIを並行して検証する。実認証、役割/tenant境界、旧クライアントの経路、新しい写真session/生成job、CORS、画像/帳票の動作を比較する。旧新workerを同じ本番table/streamへ無調整で接続することは、並行検証とは扱わない。
3. 本番更新前にCloudFormation変更セットのAPI作成/削除/置換、event source mapping、既存資源参照、保持設定を確認する。旧APIを残す方式が必要なら、別stackまたは明示的な保持・切替設計を用意する。現在のtemplateだけで旧APIが自動的に並存すると仮定しない。
4. 既存データの変更停止、旧writer/実行中Lambdaのdrain、課金・通知・生成等のイベント/queueの扱いを決める。旧新event sourceを同時に有効化して通知や請求が重複しないよう、停止・再開の順序を検証する。処理中job/CSV import/upload sessionの状態と再送先も保護する。
5. 新APIが利用可能であることを確認してから、custom domainのmappingまたはwebの`apiBaseUrl`を新stage込みのURLへ切り替える。通常の新web clientはruntime configをno-store/SW bypassで取得する。古いbundleに埋め込まれたAPI URLはruntime config更新で変わらないため、旧URL互換期間を別途決める。
6. 認証・権限・写真・帳票・イベント・課金のsmokeを確認し、監視と実利用の状況を見て旧経路を終了する。現在のCIはmock/Local検証であり、この手順を自動実行しない。

## PWA配布とrollback

FEは保存済みartifactを使い、ngsw manifestのhash検証後にhash付きassetを先行配布します。旧assetを削除せず、hashなし可変assetは同サイズ・古いmtimeでも上書きします。worker/config/manifest/indexはno-storeで更新し、CloudFront`/*`を失効させます。CloudFront最小TTL=0、deep linkからindexへのrouting、query付き取得のcache policyは実環境で確認が必要です。

S3の複数objectとCDNの更新はatomicではありません。旧PWAのoffline再開、更新中のindex/manifest不一致、旧APIに接続する開きっぱなしのclientをstagingで確認してください。hash付きassetの保存だけで旧API仕様や全旧端末への互換性を保証するものではありません。

BEは`.build/lambda`と`.aws-sam/build/template.yaml`の全ファイルをmanifestで照合します。manifest導入前の保存artifactはdeployで拒否するため、新しい`make build`で作成します。manifestは改変/混在検出用のhash一覧であり、署名ではありません。seal後のimport検査では`PYTHONDONTWRITEBYTECODE=1`を設定し、artifactを書き換えないでください。

rollback対象は新状態を理解できる互換版に限定します。[課金baseline](billing-migration.md)は台帳を無視する旧コードへ戻すと二重計上し得ます。[親子関係・写真整合性](storage-reconciliation.md)のfenceは旧writer混在時に保証できません。DB/queueの状態はアプリartifactのrollbackでは戻りません。API mappingとweb設定を戻す場合も、データ・未処理イベント・旧PWAの参照先を含めて計画します。

## このレビューでローカル検証した変更

- mutable assetの同サイズ/古いmtime rollbackをfake S3で再現。旧sync相当では失敗し、修正後のcopyでは復元する回帰テストを追加。
- 既存tableのStreams無効/欠落/異なるViewTypeを拒否し、有効な設定は再利用する管理CLIテストを追加。
- BE artifactの欠落/追加/同サイズ改変、別template混在、外部symlink、不適切なCodeUri、移設後の検証をテスト。manifestなしartifactと未指定/重複EnvironmentNameをSAM呼出前に拒否。
- 既存のLinux wheelとSAM buildを使った実artifactのmanifest作成・ローカル検証。実AWSへの配布・変更セット作成は未実施。
