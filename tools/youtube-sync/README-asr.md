# 本機語音辨識字幕草稿

`transcribe_drafts.py` 只讀本機音訊或影片、產生有時間碼的字幕草稿，不會呼叫 YouTube、讀取 OAuth 或上傳影片。所有輸出固定 `reviewed:false`，待對照原音校訂後，再把校訂好的中文與英文 SRT 加入 `catalog.json`，交給 `sync_youtube.py` 更新既有影片。

本次課堂試驗中，一般 Whisper tiny / small 對部分戶外人聲產生低信心錯字或重複文字，這些診斷稿不適合發布。可改用數位產業署與台灣大哥大維護的 [Taiwan-Tongues-ASR-CE](https://github.com/adi-gov-tw/Taiwan-Tongues-ASR-CE) 模型，比較國語、臺語及混合語音。模型是純 CTranslate2 資料，不需執行遠端程式碼；推論仍可能有誤，需依實際原音確認。

安裝可選套件到獨立環境：

```bash
python3 -m venv asr-venv
asr-venv/bin/python -m pip install -r requirements-asr.txt
```

準備本機 `asr-private/inputs.json`，例如：

```json
[
  {"videoId":"aAxfSSazhhU", "audio":"/本機/已核對的影片.mp4"}
]
```

請先核對 videoId 與影片內容，避免把其他課次或同編號檔案的字幕套上去。程式可以直接讀影片中的音訊，也可先用 FFmpeg 轉成 16kHz、單聲道 WAV。

```bash
asr-venv/bin/python transcribe_drafts.py --inputs asr-private/inputs.json --out asr-private/drafts --language zh --threads 8
```

預設使用 [官方 v1.0 CT2 模型](https://huggingface.co/adi-gov-tw/Taiwan-Tongues-ASR-CE-v1.0)，固定 revision `46b5eb7ee167ac2fd06cfe02f40e8fac416e5086`，首次會下載約 3.09 GB 權重到本機快取，後續在本機 CPU int8 運算。也能用 `--model /本機/模型目錄 --model-tag 校對用識別名稱` 指定已下載的模型，或以 `--revision commitSHA` 明確改用其他版本；輸出會記錄版本，不會把不同版本結果視為同一次可略過的工作。`--language auto` 可做語言偵測對照；官方範例以 `zh` 處理其多語模型。

每支輸出：

- `*.json`：原辨識文字、繁中轉換、逐詞時間碼、信心分數與低信心／高重複旗標。
- `*.zh-Hant.draft.srt`：未校對的時間碼字幕；無可靠語音時可能為空檔。

自動信心分數只是協助排序，不能當成字幕已正確的證明。保留不清楚的地方供回聽，不把片名或一般動作介紹填成老師的發言。英文字幕應翻譯已核對的中文稿並保留對應時間碼。

模型、音檔、含本機路徑的輸入清單及未校訂稿留在本機。官方模型採 TRAIL 授權，發布使用機器產生的內容時，應明確標示 AI 輔助語音辨識及校訂狀態。不要把模型權重或原音直接放入公開 GitHub。

## 國台語混合與本機校對頁

使用者於 2026-11-22 確認本班是國語、台語混合。官方模型以 `language=zh` 接收這種混合語音，不能將不存在的 `nan` token 傳給此模型；目標為繁體中文字幕，再由確認後的中文翻譯英文。語言設定記錄在 `spoken-language-profile.json`。

本機校對頁支援慢速播放、逐句時間碼、中文校訂及 SRT／JSON 匯出。英文可以留白，先確認中文。工具不會自動把辨識稿標為已校對，也不會呼叫 YouTube。

```bash
python3 build_caption_review.py --inputs asr-private/inputs.json --out asr-private/review --video-id aAxfSSazhhU
python3 -m http.server 8919 --bind 127.0.0.1 --directory asr-private/review
```

再用瀏覽器開啟 `http://127.0.0.1:8919/`。輸入檔使用本節前述本機音訊清單；建立工具會核對 videoId 與公開 catalog 對應，有提供來源 SHA 時也會核對。此校對資料夾含原音與未確認文字，工具會拒絕直接放進 `video-library` 或 `public` 目錄。下載校訂紀錄 JSON 或已確認 SRT 後，可沿用原 YouTube videoId 加字幕，無須重新輸出影片。
