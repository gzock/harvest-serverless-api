# 写真ストレージの整合性確認と孤児候補の調査

この手順は現行 `PhotoController` / `UploadService` の障害時挙動を対象にする。自動削除は実装しない。S3とDynamoDBを横断する原子commitはないため、参照切れ写真の削除判断を単一の一覧差分だけで行わない。

## 残る障害時の状態

- 写真作成は最終S3オブジェクトを書いてから、写真IDと件数をDynamoDB transactionで確定する。DB結果が不明な通信断ではオブジェクトを残す。削除すると、実際にはcommit済みの写真を破損させるためである。
- 新upload APIはsessionごとに固定した写真IDと隔離VersionIdを使って再試行する。確定上限超過などでDB確定できず、利用者が再試行をやめた場合、最終S3側に参照のない写真が残ることがある。
- 写真単体削除はDB参照を外してからS3を消す。S3削除に失敗した後の再送は、既にDB参照がないため通常の写真削除APIでは回収できない。
- 対象／projectの一括削除はDBに写真IDを残してS3削除を再試行できる。これは上記の単体写真削除とは順序が異なる。
- `UploadBucket` の隔離オブジェクト／旧versionにはlifecycleがある。既存の確定写真bucketは外部資源であり、このlifecycleの対象ではない。確定写真に一律expirationを追加すると、利用中の写真も失う。
- session発行制限は利用者100件／時、利用者・対象10件／15分が既定（SAM parameterで変更可）。同じpresigned POSTは有効な900秒間再使用できるため、発行制限は厳密なS3リクエスト数・総保存bytes制限ではない。VersionId固定は検証後の差替えを防ぐが、隔離bucketの重複version保管コストは残る。S3リクエスト数・保存量と費用の監視を別に行う。

## 安全にできるdry-run inventory

既存のS3 Inventory出力またはページング済み `ListObjectsV2` 出力と、Targets／UploadSessions／Projectsの読み取りsnapshotを使い、ローカルでJSON/CSVレポートを生成できる。確定bucketのS3 keyは写真IDそのもの。隔離bucketは別資源・別prefixなので混ぜない。

読み取り専用の実行権限は、対象bucketの `s3:ListBucket`（version調査時のみ `s3:ListBucketVersions`）、必要に応じ `s3:GetObjectTagging`、対象tableの `dynamodb:Scan` / `dynamodb:GetItem` に限定できる。S3 Inventory自体を新規設定する操作はbucket設定の変更であり、dry-runの読み取り処理には含めない。画像本体、署名URL、認証情報をレポートへ保存しない。

1. 対象account／region／bucket／table名、収集日時、inventory作成日時を記録する。既存S3 Inventoryは遅延するため、最新状態と同一視しない。
2. Targets全件の `photos.before` / `photos.after` / `photos.adopt` を写真IDの参照集合へ展開する。対象やprojectがdeletingでも、まだ参照がある写真は保護する。タグだけでtenant・参照の有無を判定しない。
3. UploadSessionsのquotaレコードを除き、未期限切れsessionの `photo_id` を保護集合へ加える。pinned／processing sessionはleaseの有無だけで削除候補にしない。現在のsession metadataは作成から7日保持するが、DynamoDB TTLの物理削除時刻は非同期である。
4. 確定S3一覧との差分を分類する。
   - `referenced`: Targetsから参照がある。
   - `protected_session`: 有効なsessionが使う。
   - `candidate_unreferenced`: 両集合にない。孤児の確定ではない。
   - `missing_object`: DB参照があるがinventoryにない。inventory遅延やversion/delete markerを再確認する。DB参照を自動除去しない。
5. 候補レポートへkey、LastModified、Size、ETag、VersionId（取得できる場合）、参照／session判定、snapshot時刻を出す。LastModifiedから14日未満のものは調査保留とする。この期間は安全余裕であり、14日経過だけでは削除を正当化しない。
6. 別時点の完全な読み取りでも候補であることを比較する。`Scan(ConsistentRead=True)` でも複数tableや全ページを横断するsnapshot isolationは得られない。稼働中のinventory照合結果から削除コマンドへ自動接続しない。

## 候補の回収を別途実施する場合

レビュー済みmanifestと復元手段を用意し、写真変更・upload finalize・project削除を止め、実行中のLambdaとupload leaseが終了してから全参照とsessionを再確認する。確定bucketのversioning／Object Lock／delete markerを確認し、復元できるversionまたは退避コピーを確保する。対象key・version・ETagがレビュー時と変わったものは除外する。削除はこの読み取り専用手順と分離した運用変更とし、結果を監査記録に残す。versioningがあるbucketの通常DeleteObjectは旧versionの容量を消さない点にも注意する。

## 親子関係の競合防止と切替

Place／Target作成は、子の挿入と直親Placeの `children_version` UUID更新を同じtransactionに含める。Place削除は親の強整合snapshot、Places／Targetsの強整合全ページ確認、version一致の条件付き削除で競合を検知する。既存のversion属性がないPlaceも処理でき、データ一括migrationは不要。

古いwriterはこのtokenを更新しないため、新旧writerを混在させて削除の競合防止を保証することはできない。切替時はPlace／Target変更を一時停止して旧invocationをdrainし、関係するhandlerを同じ成果物へ更新してから再開する。rollback先も同じfence規約を実装した版に限定する。
