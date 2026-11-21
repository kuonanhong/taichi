# 聰動太極拳 YouTube 與 GitHub 中英文影片管理

本工具維護既有影片的中英文標題、說明、字幕軌及播放清單。公開資料位於 `../../video-library/`，與原太極拳遊戲分開。此版的說明是依既有題名整理的觀看指引草稿，尚未逐支核對老師口述，也尚未寫入 YouTube。公開清單收錄 2026-11-19 核對過的 29 個影片 ID；它不是當日完整頻道清冊，第一次授權後應先重新 inventory。

中文原題保留，英文使用 YouTube `localizations.en`。若拿不到原始上傳檔名，`sourceFilename` 保持 null，不猜測檔名。字幕目前沒有轉錄和校對，因此 `captions` 留空，不能將說明冒充字幕。

## 先回答常見問題

- 已上傳的影片可直接加上繁體中文、英文兩條可選字幕，或修改錯字；不用重做或重傳 MP4，影片網址與既有觀看數保留。只有要把兩種文字永久壓在畫面上時，才需要重新輸出影片。YouTube 不提供用新影片檔取代原影片檔且沿用原 ID 的一般 API。
- 名稱、短介紹、詳細說明、字幕是不同資料。英文標題與說明放在語言欄位；字幕需另製作有時間碼的 SRT/VTT，供觀眾在 CC 選單切換。兩條語言軌不等於同時顯示雙語；若想同時顯示，可另作每段包含中文及英文的雙語軌。
- YouTube 影片標題上限 100 字元；目前 Data API 文件標示 description 上限 5,000 UTF-8 bytes，不應理解為 5,000 個中文字。工具會檢查長度，說明無須為了填滿上限而堆字。
- GitHub 沒有 YouTube 原生播放清單功能。本專案的 `video-library` 是分類目錄，影片在 YouTube 播放；字幕檔可在 GitHub 版本管理。影片直接在 GitHub 網頁播放不會因此變成 YouTube 觀看。YouTube 依自己的規則判定有效觀看。
- 無字幕時，要先以原音做語音辨識、繁中校對、英文翻譯及對時。API 不會憑影片題名生成正確逐字字幕。若是影片內已燒錄的文字，字幕 API 也無法移除它。

## 建立 YouTube OAuth（只需本人第一次授權）

1. 在 [Google Cloud](https://console.cloud.google.com/) 選擇或建立協會的專案，啟用 [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com)。
2. 在 Google Auth Platform 設定 Branding、Audience 及 Data Access。若為 External / Testing，把實際管理頻道的 Google 帳號加入 Test users；測試狀態的 refresh token 通常七天過期，長期自動化需依 Google 規範調整發布狀態與所需驗證。
3. 在 Clients 建立 **Desktop app（桌面應用程式）OAuth Client ID**，下載 JSON。這是「應用程式」ID，不是 Gmail 地址、YouTube 頻道 ID，也不是 API Key。
4. 把 JSON 存在本機私有位置，例如 `~/.config/smartaction-youtube/client_secret.json`，不要放到公開 GitHub、公開網頁或貼到聊天。
5. 執行下述 `auth`，在系統瀏覽器選擇真正管理「高雄市聰動成長協會」頻道的帳號與頻道。程式使用本機 callback 接收授權，保存私有 token；不使用 Gmail 密碼。

Scope 為 `https://www.googleapis.com/auth/youtube.force-ssl`，供字幕、影片及清單編輯。只給 `youtube.upload` 不足以編輯字幕與清單。首次授權後可用 refresh token 重新取得短效 access token；帳號撤銷授權、token 失效或專案狀態改變仍可能需要重新登入。

## 安裝與使用

需要 Python 3.10 以上。先從專案根目錄進入：

```bash
cd tools/youtube-sync
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
python3 -m unittest discover -v
```

具體命令與參數見 `python3 sync_youtube.py --help`。`plan` 不需安裝 Google 套件，也不需登入；不加明確寫入參數不會變更 YouTube。先檢查預覽，再逐支核對檔名、說明與字幕，將該筆 `reviewed` 改為 true 才供寫入。這是內容校對欄位，並非每天都要重新取得帳號權限。

```bash
# 純本機預覽，不需 OAuth
python3 sync_youtube.py plan

# 首次授權，請本人在開啟的系統瀏覽器選擇正確頻道
.venv/bin/python sync_youtube.py auth --client-secrets ~/.config/smartaction-youtube/client_secret.json

# 重新盤點既有影片；私有檔案輸出到 config 目錄
.venv/bin/python sync_youtube.py inventory --out ~/.config/smartaction-youtube/inventory.json

# 將 UC_CHANNEL_ID 換成 auth 實際列出的正確頻道 ID
# 下載既有字幕，英文機翻版保持草稿待校對
.venv/bin/python sync_youtube.py export-captions --channel-id UC_CHANNEL_ID --video-id aAxfSSazhhU --out ~/.config/smartaction-youtube/captions-export --english-translation

# 校訂字幕及 metadata、把對應影片和分類標為 reviewed:true 後
.venv/bin/python sync_youtube.py sync --channel-id UC_CHANNEL_ID
.venv/bin/python sync_youtube.py sync --channel-id UC_CHANNEL_ID --apply
```

字幕 metadata 範例（`file` 相對於 catalog.json，下面檔名為範例，實際需先建立檔案）：

```json
"captions": [
  {"language": "zh-Hant", "file": "captions/aAxfSSazhhU.zh-Hant.srt", "reviewed": true},
  {"language": "en", "file": "captions/aAxfSSazhhU.en.srt", "reviewed": true}
]
```

新影片使用另一份本機 catalog，加入 `localFile`、`sha256`、`privacyReviewed:true`、`newUploadConfirmed:true`、`reviewed:true`，不填 `videoId`；再執行 `upload --catalog /本機/catalog.json --record-key 新片索引 --channel-id UC_CHANNEL_ID --apply`。只接受已核對 SHA256 的修正版影片，首次上傳固定 private 並關閉訂閱通知。成功回傳 videoId 後寫回 catalog，再執行 sync 加字幕與分類。已有人手動上傳、卻沒有本程式的 SHA 標記時，必須先認領正確既有 videoId；YouTube 不提供查詢原影片雜湊的 API，本程式不能替所有歷史人工上傳保證去重。

字幕檔請使用 UTF-8，有開始及結束時間碼。YouTube API 的 `sync` 自動對時功能已停止支援；YouTube Studio 畫面上的對時功能與 API 不是同一介面。

## 防止重複與保留原資料

工具更新既有 video ID，保留舊 tags、categoryId、defaultAudioLanguage 和其他語言；不更新既有影片的公開狀態。字幕只管理 `SmartAction 繁體中文` 及 `SmartAction English` 兩種命名的自有軌，不刪原字幕。新增清單和清單成員前會讀回分頁結果；既有主清單 `PLSFQlDsijCrs` 留存。

本機同步紀錄不放在 GitHub。涉及新增的遠端操作遇到不確定回應時要先核對，不以「看不到回應」當成失敗而盲目重傳。實際寫入功能仍需先完成 OAuth 與正確頻道核對；本次離線測試不能當作已驗證真實帳號寫入。

## 更新 GitHub 的影片目錄和字幕

修改 `video-library/catalog.json` 及相關字幕／頁面後執行：

```bash
python3 publish_github.py
# 預覽差異正確後，將僅有指定 repository Contents:read/write 權限的
# fine-grained token 安全地放入本機 GITHUB_TOKEN 環境變數，再執行：
python3 publish_github.py --apply
```

此程式只處理 `video-library/` 的文字檔，不刪遠端舊資料、不改遊戲，沒有差異就略過。它以目前 tree 為基礎建立單次 commit，並只作 fast-forward 更新。權限金鑰與字幕初稿含個資的內容不應公開。大型影片建議留在 YouTube 或既有媒體空間；一般 Git 檔案有 100 MiB 限制。

修改 JSON 後，先執行 `python3 render_catalog.py` 重新產生預覽頁，再執行 `publish_github.py`。

影片目錄預覽與 YouTube 寫入是兩個獨立動作：GitHub 的 commit 不會自行修改 YouTube；YouTube 成功更新後，也要明確更新／發布 GitHub 的資料。

## 官方文件

- [桌面應用 OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
- [影片欄位、標題及說明限制](https://developers.google.com/youtube/data/reference/rest/v3/videos)
- [更新影片與翻譯欄位](https://developers.google.com/youtube/data/reference/rest/v3/videos/update)
- [上傳字幕](https://developers.google.com/youtube/data/reference/rest/v3/captions/insert)、[更新字幕](https://developers.google.com/youtube/data/reference/rest/v3/captions/update)、[下載字幕及機器翻譯](https://developers.google.com/youtube/data/reference/rest/v3/captions/download)
- [上傳影片](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert)、[可續傳協定](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol)
- [播放清單](https://developers.google.com/youtube/data/reference/rest/v3/playlists/insert)、[清單成員](https://developers.google.com/youtube/data/reference/rest/v3/playlistItems/insert)
- [GitHub 大檔案限制](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
