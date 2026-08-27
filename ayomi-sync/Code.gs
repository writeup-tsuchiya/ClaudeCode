/**
 * ヨミ表（スプレッドシート1）→ Ａヨミ案件（スプレッドシート2）同期
 *
 * ・ヨミ表のP列ステータスが「Aヨミ*」または「受注」になったら、Ａヨミ案件へ行を転記する
 * ・転記済みの行は、その後のヨミ表側の変更（ステータス・商談日など）をＡヨミ案件に上書き反映する
 * ・Ａヨミ案件のステータスは「受注 / Aヨミ / 失注」の3つだけに丸める
 * ・同期は ヨミ表 → Ａヨミ案件 の一方向のみ
 *
 * 【重要】このプロジェクトには他のスクリプトが同居しており、Apps Script は
 * 全ファイルが同じ名前空間を共有する。名前がぶつかると片方が黙って壊れるので、
 * このファイルが外に出す名前は AYOMI_ で始まるものだけに閉じてある。
 * （中身はすべて下の即時関数の中にあるので、他のファイルからは見えない）
 *
 * 設置先：スプレッドシート1（ヨミ表があるファイル）の Apps Script
 * 初回手順：AYOMI_setupStatusDropdown() → AYOMI_initialLink() → AYOMI_setupTriggers()
 */

const AYOMI_VERSION = '2026-08-27e（名前空間を分離）';

// ===== 設定 =========================================================

const AYOMI_CONFIG = {
  SRC: {
    ID: '1t7pVp895kKB0vWWHbq-h_hfDAp0HkW5oc1cW20HeCtQ',
    SHEET: 'ヨミ表',
    COL_OWNER: 1,      // A 担当営業
    COL_COMPANY: 2,    // B 会社名
    COL_LAST_NAME: 3,  // C 担当名（姓）
    COL_STATUS: 16,    // P ステータス
    COL_FIRST_MTG: 18, // R 初回商談日
    COL_LINK: 54,      // BB Aヨミリスト転記
  },
  DST: {
    ID: '1svI03RoBoqNtrhpWBSCJ8_W4uaPQBcRvEfIpw9D_hH0',
    SHEET: 'Ａヨミ案件',
    COL_OWNER: 1,      // A 担当営業
    COL_COMPANY: 2,    // B 会社名
    COL_LAST_NAME: 3,  // C 担当名（姓）
    COL_STATUS: 16,    // P ステータス
    COL_LINK: 54,      // BB Aヨミリスト転記（無ければ自動でヘッダーを作る）
  },

  // 対象にする初回商談日の下限（この日以降の案件だけ同期する）
  TARGET_FROM: new Date(2026, 6, 20), // 2026/07/20

  LINK_PREFIX: 'A-',    // 転記ナンバーの接頭辞（A-0001 …）
  EXCLUDE_MARK: '対象外', // BB列にこの文字が入っている行は同期しない
  LINK_HEADER: 'Aヨミリスト転記',
  LOG_SHEET: '_同期ログ',
  LOG_MAX_ROWS: 5000,

  /**
   * Ａヨミ案件のP列に入れてよい値。
   * AYOMI_setupStatusDropdown() を実行すると、この3つだけのプルダウンに張り替える。
   */
  DST_STATUS_VALUES: ['受注', 'Aヨミ', '失注'],

  /**
   * ステータスの言い換え：ヨミ表の値 → Ａヨミ案件に書き込む値。
   * 上から順に判定し、最初に当たったものを使う（全角半角・空白を吸収した後の値で判定）。
   */
  STATUS_MAP: [
    { when: /^Aヨミ/, to: 'Aヨミ' }, // Aヨミ（決裁者口頭合意済）/（決裁者確認中）など
    { when: /^受注$/, to: '受注' },  // 「受注後キャンセル」は下の失注に落ちる
    { when: /^/,      to: '失注' },  // 上記以外（Cヨミ・リスケ・追客NG・受注後キャンセル…）
  ],

  /**
   * 担当営業の名寄せ（手動指定）。ヨミ表の表記 → Ａヨミ案件の表記。
   * 通常は自動照合（Ａヨミ案件の呼び名がヨミ表の氏名に含まれるか）で解決するので空でよい。
   * 「渡辺→ナベさん」のように文字が重ならないケースだけここに書く。
   */
  OWNER_MAP: {
    // '菅野敬彦': '敬彦',
  },

  /**
   * 列マッピング： { src: ヨミ表の列番号, dst: Ａヨミ案件の列番号 }
   * A〜Q列は両シートで同じ並びなので 1:1。R列以降は並びが違うので個別に対応させる。
   *
   * 【同期しない列】Ａヨミ案件の U / V回収予定日 / Wメモ / X初期費用 / Y月額費用 /
   * Z期間 / AA合計金額 は、ヨミ表に対応する列が無い（または単位が違う）ため触らない。
   * ＝ Ａヨミ案件側で手入力した内容は上書きで消えない。
   */
  SYNC_MAP: [
    { src: 1,  dst: 1 },  // A 担当営業（表記が違う場合は名寄せしてから書き込む）
    { src: 2,  dst: 2 },  // B 会社名
    { src: 3,  dst: 3 },  // C 担当名（姓）
    { src: 4,  dst: 4 },  // D 担当名（名）
    { src: 5,  dst: 5 },  // E 役職
    { src: 6,  dst: 6 },  // F 正社員数
    { src: 7,  dst: 7 },  // G 創業年
    { src: 8,  dst: 8 },  // H 住所（都道府県）
    { src: 9,  dst: 9 },  // I 住所（市区町村）
    { src: 10, dst: 10 }, // J 事業内容 → 業種
    { src: 11, dst: 11 }, // K メールアドレス
    { src: 12, dst: 12 }, // L 電話番号
    { src: 13, dst: 13 }, // M HP
    { src: 14, dst: 14 }, // N アルバイト数
    { src: 15, dst: 15 }, // O 紹介元代理店
    { src: 16, dst: 16 }, // P ステータス
    { src: 17, dst: 17 }, // Q セミ参加日
    { src: 21, dst: 18 }, // U 受注日        → R 受注日
    { src: 18, dst: 19 }, // R 初回商談日     → S 初回商談日
    { src: 19, dst: 20 }, // S 2回目商談日    → T 2回目商談日
    { src: 23, dst: 28 }, // W 受注商品数     → AB 受注商品数
    { src: 24, dst: 29 }, // X 提案商品/受注商品 → AC 商品
  ],
};

// ===== メニューから実行する関数 =====================================
// Apps Script の関数プルダウンに出るのはこの7つだけ。中身は下の即時関数の中。

/** 【初回1】Ａヨミ案件のP列を「受注 / Aヨミ / 失注」の3択に固定する */
function AYOMI_setupStatusDropdown() { return AYOMI.setupStatusDropdown(); }

/** 【初回2】いまAヨミ／受注の行を整理する（既存分の一括流し込みを防ぐ） */
function AYOMI_initialLink() { return AYOMI.initialLink(); }

/** 【初回3】編集時トリガーと1時間ごとのトリガーを設置する */
function AYOMI_setupTriggers() { return AYOMI.setupTriggers(); }

/** 【任意】「対象外」になった行を会社名だけで突合し直す */
function AYOMI_relinkByCompany() { return AYOMI.relinkByCompany(); }

/** 【任意】全行を今すぐ同期する（手動実行用） */
function AYOMI_syncNow() { return AYOMI.syncAll(); }

/** 【診断】AYOMI_debug と入力して実行。会社名を渡すとその案件を調べる */
function AYOMI_debug(companyKeyword) { return AYOMI.debug(companyKeyword); }

/** 編集時トリガーの受け口（手で実行しないこと） */
function AYOMI_onEdit(e) { return AYOMI.onEdit(e); }

/** 時間主導トリガーの受け口（手で実行しないこと） */
function AYOMI_syncAll() { return AYOMI.syncAll(); }

// ===== 本体（この中の名前は外から見えない）==========================

const AYOMI = (function () {
  const CONFIG = AYOMI_CONFIG;

  // ----- 初回セットアップ -------------------------------------------

  /**
   * いま既にヨミ表にあるAヨミ／受注の行を「今後変わった分だけ同期」の状態に整える。
   *   ・Ａヨミ案件に同じ案件（会社名＋姓）が既にある → 転記ナンバーを両方に振って紐づけるだけ
   *   ・Ａヨミ案件に無い                          → BB列に「対象外」を入れて一括流し込みを防ぐ
   * あとから転記したくなったら、その行のBB列を空にすれば次回の同期で転記される。
   */
  function initialLink() {
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(30 * 1000)) throw new Error('他の同期処理が実行中です。少し待って再実行してください。');
    try {
      const ctx = buildContext();
      let linked = 0, excluded = 0, skipped = 0;

      for (let i = 0; i < ctx.srcRows.length; i++) {
        const row = ctx.srcRows[i];
        const rowNo = ctx.srcHeaderRow + 1 + i;
        if (isBlankRow(row)) continue;

        const link = String(row[CONFIG.SRC.COL_LINK - 1] || '').trim();
        if (link) { skipped++; continue; } // 既に番号や「対象外」が入っている行は触らない
        if (!isTargetRow(row)) { skipped++; continue; }

        const key = rowKey(row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_LAST_NAME - 1]);
        const hit = key ? ctx.dstByKey[key] : null;

        try {
          if (hit) {
            const no = ctx.nextLinkNo();
            ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
            ctx.dstSheet.getRange(hit.rowNo, CONFIG.DST.COL_LINK).setValue(no);
            ctx.dstByLink[no] = hit;
            linked++;
            addLog(ctx, 'リンク', no, row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
                   'ヨミ表' + rowNo + '行 ↔ Ａヨミ案件' + hit.rowNo + '行');
          } else {
            ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(CONFIG.EXCLUDE_MARK);
            excluded++;
            addLog(ctx, '対象外', '', row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
                   '初期セットアップ時の既存行のため転記しない（ヨミ表' + rowNo + '行）');
          }
        } catch (err) {
          addLog(ctx, 'エラー', '', row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
                 'ヨミ表' + rowNo + '行： ' + errText(err));
        }
      }
      flushLog(ctx);
      const msg = '初期セットアップ完了： 紐づけ ' + linked + '件 ／ 対象外 ' + excluded + '件 ／ 変更なし ' + skipped + '件';
      Logger.log(msg);
      return msg;
    } finally {
      lock.releaseLock();
    }
  }

  /**
   * 「対象外」になった行を、会社名だけで突合し直して紐づける。
   * initialLink() は 会社名＋姓 で突合するので、姓の入れ方が違う（片方が「横田」、
   * 片方が「横田徳」）と一致せず「対象外」になる。その取りこぼしを拾う。
   */
  function relinkByCompany() {
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(30 * 1000)) throw new Error('他の同期処理が実行中です。少し待って再実行してください。');
    try {
      const ctx = buildContext();
      let linked = 0;

      // Ａヨミ案件の「まだ番号が無い行」を会社名だけで引けるようにする
      const dstLast = ctx.dstSheet.getLastRow();
      const dstWidth = Math.max(ctx.dstSheet.getLastColumn(), CONFIG.DST.COL_LINK);
      const dstRows = dstLast > ctx.dstHeaderRow
        ? ctx.dstSheet.getRange(ctx.dstHeaderRow + 1, 1, dstLast - ctx.dstHeaderRow, dstWidth).getValues()
        : [];
      const freeByCompany = {};
      dstRows.forEach(function (r, i) {
        if (String(r[CONFIG.DST.COL_LINK - 1] || '').trim()) return;
        const c = companyKey(r[CONFIG.DST.COL_COMPANY - 1]);
        if (c && !freeByCompany[c]) freeByCompany[c] = { rowNo: ctx.dstHeaderRow + 1 + i };
      });

      for (let i = 0; i < ctx.srcRows.length; i++) {
        const row = ctx.srcRows[i];
        const rowNo = ctx.srcHeaderRow + 1 + i;
        if (String(row[CONFIG.SRC.COL_LINK - 1] || '').trim() !== CONFIG.EXCLUDE_MARK) continue;

        const c = companyKey(row[CONFIG.SRC.COL_COMPANY - 1]);
        const hit = c ? freeByCompany[c] : null;
        if (!hit) continue;

        const no = ctx.nextLinkNo();
        ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
        ctx.dstSheet.getRange(hit.rowNo, CONFIG.DST.COL_LINK).setValue(no);
        delete freeByCompany[c];
        linked++;
        addLog(ctx, 'リンク', no, row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
               '会社名で再突合（ヨミ表' + rowNo + '行 ↔ Ａヨミ案件' + hit.rowNo + '行）');
      }
      flushLog(ctx);
      const msg = '会社名での再突合： ' + linked + '件を紐づけました';
      Logger.log(msg);
      return msg;
    } finally {
      lock.releaseLock();
    }
  }

  /** Ａヨミ案件のP列のプルダウンを「受注 / Aヨミ / 失注」の3つに固定する */
  function setupStatusDropdown() {
    const dstSheet = getSheet(SpreadsheetApp.openById(CONFIG.DST.ID), CONFIG.DST.SHEET);
    const headerRow = findHeaderRow(dstSheet, CONFIG.DST.COL_STATUS);
    const rows = dstSheet.getMaxRows() - headerRow;
    if (rows < 1) return 'Ａヨミ案件にデータ行がありません';

    const rule = SpreadsheetApp.newDataValidation()
      .requireValueInList(CONFIG.DST_STATUS_VALUES, true)
      .setAllowInvalid(false)
      .build();
    dstSheet.getRange(headerRow + 1, CONFIG.DST.COL_STATUS, rows, 1).setDataValidation(rule);

    const msg = 'Ａヨミ案件のP列を「' + CONFIG.DST_STATUS_VALUES.join(' / ') + '」の3択に固定しました';
    Logger.log(msg);
    return msg;
  }

  /**
   * 編集時トリガー（即時）と1時間ごとの差分チェックを設置する。二重登録はしない。
   * このスクリプトが作ったトリガーだけを消すので、同居している他スクリプトのトリガーは触らない。
   */
  function setupTriggers() {
    const mine = ['AYOMI_onEdit', 'AYOMI_syncAll', 'onEditYomi', 'syncAll']; // 後ろ2つは旧版の名前
    let removed = 0;
    ScriptApp.getProjectTriggers().forEach(function (t) {
      if (mine.indexOf(t.getHandlerFunction()) >= 0) { ScriptApp.deleteTrigger(t); removed++; }
    });
    const ss = SpreadsheetApp.openById(CONFIG.SRC.ID);
    ScriptApp.newTrigger('AYOMI_onEdit').forSpreadsheet(ss).onEdit().create();
    ScriptApp.newTrigger('AYOMI_syncAll').timeBased().everyHours(1).create();

    const msg = 'トリガーを設置しました（編集時：即時 ／ 時間主導：1時間ごと）'
              + (removed ? ' ※古いトリガー' + removed + '件を削除' : '');
    Logger.log(msg);
    return msg;
  }

  // ----- トリガーの中身 ---------------------------------------------

  /** ヨミ表の編集行だけを同期する */
  function onEdit(e) {
    if (!e || !e.range) return;
    const sheet = e.range.getSheet();
    if (normalizeName(sheet.getName()) !== normalizeName(CONFIG.SRC.SHEET)) return;

    // 同期のきっかけになる列（ステータス／初回商談日／転記ナンバー）が無ければ何もしない
    const c1 = e.range.getColumn();
    const c2 = c1 + e.range.getNumColumns() - 1;
    const watch = [CONFIG.SRC.COL_STATUS, CONFIG.SRC.COL_FIRST_MTG, CONFIG.SRC.COL_LINK];
    if (!watch.some(function (c) { return c >= c1 && c <= c2; })) return;

    const rows = [];
    for (let r = e.range.getRow(); r < e.range.getRow() + e.range.getNumRows(); r++) rows.push(r);
    return sync(rows);
  }

  /** 全行をチェックする（編集トリガーの取りこぼし対策） */
  function syncAll() {
    return sync(null);
  }

  // ----- 同期本体 ---------------------------------------------------

  /** @param {Array<number>|null} onlyRows 対象にするヨミ表の行番号。null なら全行 */
  function sync(onlyRows) {
    const lock = LockService.getScriptLock();
    if (!lock.tryLock(30 * 1000)) {
      // 別の実行が長引いていると編集時の同期がここで捨てられる。気づけるようログに残す。
      const tmp = { logBuffer: [] };
      addLog(tmp, 'エラー', '', '', '', '他の同期処理が実行中だったため、この編集の同期を見送った（30秒待ってもロックが取れず）');
      try { flushLog(tmp); } catch (e) { /* ログすら書けなければ諦める */ }
      Logger.log('同期スキップ：他の処理が実行中');
      return;
    }
    try {
      const ctx = buildContext();
      ctx.verbose = !!onlyRows; // 編集起点のときだけ「見送り」もログに残す
      const result = { added: 0, updated: 0, skipped: 0, error: 0 };

      for (let i = 0; i < ctx.srcRows.length; i++) {
        const rowNo = ctx.srcHeaderRow + 1 + i;
        if (onlyRows && onlyRows.indexOf(rowNo) < 0) continue;
        try {
          syncOneRow(ctx, ctx.srcRows[i], rowNo, result);
        } catch (err) {
          result.error++;
          addLog(ctx, 'エラー', '', ctx.srcRows[i][CONFIG.SRC.COL_COMPANY - 1], '',
                 'ヨミ表' + rowNo + '行： ' + errText(err));
        }
      }
      flushLog(ctx);
      Logger.log('同期完了： 新規 %s / 更新 %s / 対象外 %s / エラー %s',
                 result.added, result.updated, result.skipped, result.error);
      return result;
    } finally {
      lock.releaseLock();
    }
  }

  function syncOneRow(ctx, row, rowNo, result) {
    if (isBlankRow(row)) { result.skipped++; return; }

    const linkRaw = String(row[CONFIG.SRC.COL_LINK - 1] || '').trim();
    if (linkRaw === CONFIG.EXCLUDE_MARK) { result.skipped++; return; }

    const company = row[CONFIG.SRC.COL_COMPANY - 1];
    const status = row[CONFIG.SRC.COL_STATUS - 1];

    // --- 転記済みの行：ステータスが何に変わっても追随して上書きする ---
    if (linkRaw) {
      let hit = ctx.dstByLink[linkRaw];
      if (!hit) {
        // Ａヨミ案件側の行が消えている等 → 同じ番号で作り直す
        hit = appendDstRow(ctx, row, linkRaw);
        addLog(ctx, '再作成', linkRaw, company, status, 'Ａヨミ案件に該当行が無かったため再追加');
        result.added++;
        return;
      }
      if (writeDstRow(ctx, hit.rowNo, row)) {
        result.updated++;
        addLog(ctx, '更新', linkRaw, company, status, 'Ａヨミ案件' + hit.rowNo + '行を上書き');
      } else {
        result.skipped++;
      }
      return;
    }

    // --- 未転記の行：Aヨミ／受注で、初回商談日が対象期間内なら転記する ---
    if (!isTargetRow(row)) {
      result.skipped++;
      if (ctx.verbose && isTargetStatus(status)) {
        const d = parseDateValue(row[CONFIG.SRC.COL_FIRST_MTG - 1]);
        addLog(ctx, '見送り', '', company, status,
               'ヨミ表' + rowNo + '行： 初回商談日（R列）が ' +
               (d ? Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy/MM/dd') + ' で対象期間外'
                  : '未入力または日付として読めない'));
      }
      return;
    }

    const key = rowKey(company, row[CONFIG.SRC.COL_LAST_NAME - 1]);
    const existing = key ? ctx.dstByKey[key] : null;
    const no = ctx.nextLinkNo();

    if (existing) {
      // 手入力で既にＡヨミ案件にある案件 → 重複させず、その行に紐づけて更新
      ctx.dstSheet.getRange(existing.rowNo, CONFIG.DST.COL_LINK).setValue(no);
      ctx.dstByLink[no] = existing;
      writeDstRow(ctx, existing.rowNo, row, true);
      ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
      result.updated++;
      addLog(ctx, '紐づけ更新', no, company, status,
             '既存行と一致（Ａヨミ案件' + existing.rowNo + '行）のため新規追加せず更新');
      return;
    }

    const added = appendDstRow(ctx, row, no);
    ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
    result.added++;
    addLog(ctx, '新規', no, company, status, 'Ａヨミ案件' + added.rowNo + '行に追加');
  }

  /**
   * マッピング対象の転記先列を、連続した列のかたまり（ブロック）にまとめる。
   * マッピングに無い列（手入力列や数式列）には一切触れないようにするため。
   */
  function syncBlocks() {
    const map = CONFIG.SYNC_MAP.slice().sort(function (a, b) { return a.dst - b.dst; });
    const blocks = [];
    map.forEach(function (m) {
      const last = blocks[blocks.length - 1];
      if (last && m.dst === last.start + last.cols.length) {
        last.cols.push(m.src);
      } else {
        blocks.push({ start: m.dst, cols: [m.src] });
      }
    });
    return blocks;
  }

  /** Ａヨミ案件の最終行に1行追加する */
  function appendDstRow(ctx, srcRow, linkNo) {
    const rowNo = Math.max(ctx.dstSheet.getLastRow(), ctx.dstHeaderRow) + 1;
    writeDstRow(ctx, rowNo, srcRow, true);
    ctx.dstSheet.getRange(rowNo, CONFIG.DST.COL_LINK).setValue(linkNo);

    const entry = { rowNo: rowNo };
    ctx.dstByLink[linkNo] = entry;
    const key = rowKey(srcRow[CONFIG.SRC.COL_COMPANY - 1], srcRow[CONFIG.SRC.COL_LAST_NAME - 1]);
    if (key && !ctx.dstByKey[key]) ctx.dstByKey[key] = entry;
    return entry;
  }

  /**
   * Ａヨミ案件の1行を、マッピング対象の列だけ書き込む。変更があれば true。
   *
   * 転記先の列にはプルダウン（データの入力規則）があり、候補に無い値を書こうとすると
   * setValues ごと例外になる。そうなると同じ塊にある他の列（ステータス等）まで
   * 巻き添えで書き込まれないので、弾かれるセルだけ事前に外して書き込む。
   *
   * @param {boolean} logSkips 弾かれたセルをログに残すか（毎時の全件チェックでは
   *                           同じ行が何度も積もるので、新規転記時と編集起点だけ true）
   */
  function writeDstRow(ctx, dstRowNo, srcRow, logSkips) {
    const company = srcRow[CONFIG.SRC.COL_COMPANY - 1];
    const noteSkip = logSkips || ctx.verbose;
    let changed = false;

    syncBlocks().forEach(function (b) {
      const range = ctx.dstSheet.getRange(dstRowNo, b.start, 1, b.cols.length);
      const current = range.getValues()[0];
      const allowed = allowedValues(ctx.dstSheet, dstRowNo, b.start, b.cols.length);
      const next = current.slice();
      let blockChanged = false;

      b.cols.forEach(function (srcCol, i) {
        const dstCol = b.start + i;
        let v = srcValue(srcRow, srcCol);
        if (dstCol === CONFIG.DST.COL_OWNER) {
          const named = resolveOwner(ctx, dstCol, v, allowed[i]);
          if (named !== null) v = named;
        }
        if (sameCell(current[i], v)) return;
        if (rejectedBy(allowed[i], v)) {
          if (noteSkip) {
            addLog(ctx, 'スキップ', '', company, '',
                   'Ａヨミ案件 ' + colLetter(dstCol) + dstRowNo + ' は入力規則にない値なので書き込まなかった： 「' +
                   v + '」（候補： ' + allowed[i].join(' / ') + '）');
          }
          return;
        }
        next[i] = v;
        blockChanged = true;
      });
      if (!blockChanged) return;

      try {
        range.setValues([next]);
        changed = true;
      } catch (err) {
        // 規則を読み切れなかった場合の保険：1セルずつ書いて、弾かれたセルだけ飛ばす
        b.cols.forEach(function (srcCol, i) {
          if (sameCell(current[i], next[i])) return;
          try {
            ctx.dstSheet.getRange(dstRowNo, b.start + i).setValue(next[i]);
            changed = true;
          } catch (e2) {
            if (noteSkip) {
              addLog(ctx, 'スキップ', '', company, '',
                     'Ａヨミ案件 ' + colLetter(b.start + i) + dstRowNo + ' が入力規則で拒否された： 「' + next[i] + '」');
            }
          }
        });
      }
    });
    return changed;
  }

  /** ヨミ表の値を、Ａヨミ案件に書き込む値に変換する（ステータスだけ言い換える） */
  function srcValue(srcRow, srcCol) {
    const raw = toCell(srcRow[srcCol - 1]);
    return srcCol === CONFIG.SRC.COL_STATUS ? mapStatus(raw) : raw;
  }

  /**
   * ヨミ表のステータスを「受注 / Aヨミ / 失注」に丸める。
   * ヨミ表が空欄のときは空欄のまま（勝手に失注にしない）。
   */
  function mapStatus(v) {
    const s = normalizeStatus(v);
    if (!s) return '';
    for (let i = 0; i < CONFIG.STATUS_MAP.length; i++) {
      if (CONFIG.STATUS_MAP[i].when.test(s)) return CONFIG.STATUS_MAP[i].to;
    }
    return toCell(v);
  }

  /**
   * 担当営業を、Ａヨミ案件で使われている呼び名に寄せる。
   * ヨミ表「土屋慧介」→「土屋」、「菅野敬彦」→「敬彦」のように、
   * 「候補がヨミ表の氏名に含まれる（または逆）」で1つに絞れたらその候補を使う。
   * 絞れなければ null を返し、呼び出し側でスキップ扱いにする。
   */
  function resolveOwner(ctx, dstCol, value, allowed) {
    if (dstCol !== CONFIG.DST.COL_OWNER) return null;
    const raw = String(value == null ? '' : value).trim();
    if (!raw) return null;

    if (CONFIG.OWNER_MAP[raw]) return CONFIG.OWNER_MAP[raw];

    // プルダウンがあればその候補、無ければＡヨミ案件で既に使われている呼び名から探す
    const candidates = (allowed && allowed.length) ? allowed : ctx.dstOwners;
    if (!candidates || !candidates.length) return null;

    const v = normalizeName(raw);
    const hits = candidates.filter(function (c) {
      const n = normalizeName(c);
      return n && (v.indexOf(n) >= 0 || n.indexOf(v) >= 0);
    });
    return hits.length === 1 ? hits[0] : null; // 複数に当たったら曖昧なので書かない
  }

  // ----- 入力規則 ---------------------------------------------------

  /** 各セルの「入力規則で許可された値」の配列。制限が無い（警告のみ含む）列は null */
  function allowedValues(sheet, rowNo, startCol, numCols) {
    let dvs;
    try {
      dvs = sheet.getRange(rowNo, startCol, 1, numCols).getDataValidations()[0];
    } catch (e) {
      return new Array(numCols).fill(null);
    }
    return dvs.map(function (dv) { return dvAllowed(dv); });
  }

  function dvAllowed(dv) {
    if (!dv) return null;
    try {
      if (dv.getAllowInvalid()) return null; // 「警告を表示」だけの規則は書き込めるので素通し
      const C = SpreadsheetApp.DataValidationCriteria;
      const type = dv.getCriteriaType();
      const args = dv.getCriteriaValues();
      if (type === C.VALUE_IN_LIST) {
        return args[0].map(function (v) { return String(v).trim(); });
      }
      if (type === C.VALUE_IN_RANGE) {
        const out = [];
        args[0].getValues().forEach(function (r) {
          r.forEach(function (v) { if (String(v).trim()) out.push(String(v).trim()); });
        });
        return out;
      }
    } catch (e) { /* 読み取れない規則は素通しし、書き込み時の例外で拾う */ }
    return null;
  }

  /** その値が入力規則に弾かれるか */
  function rejectedBy(allowed, value) {
    if (!allowed || !allowed.length) return false;
    if (value instanceof Date) return false; // 日付のリスト規則は想定しない
    const s = String(value == null ? '' : value).trim();
    if (!s) return false;                    // 空欄は規則に関わらず書ける
    return allowed.indexOf(s) < 0;
  }

  // ----- 判定・小道具 -----------------------------------------------

  /** 転記のきっかけになるステータスか（Aヨミ or 受注） */
  function isTargetStatus(v) {
    const s = normalizeStatus(v);
    return s.indexOf('Aヨミ') === 0 // 「Aヨミ（決裁者口頭合意済）」等も拾う
        || s === '受注';            // 「受注後キャンセル」は拾わない
  }

  /** 未転記の行を転記対象とみなすか（Aヨミ or 受注 かつ 初回商談日が対象期間内） */
  function isTargetRow(row) {
    if (!isTargetStatus(row[CONFIG.SRC.COL_STATUS - 1])) return false;
    const d = parseDateValue(row[CONFIG.SRC.COL_FIRST_MTG - 1]);
    if (!d) return false;
    return d.getTime() >= CONFIG.TARGET_FROM.getTime();
  }

  /**
   * ステータスの表記ゆれを吸収する。
   * 「Ａヨミ」「a ヨミ」「Ａヨミ（決裁者口頭合意済）」→ いずれも「Aヨミ…」になる。
   * （！-～ は全角ASCIIの範囲。Ａ-Ｚ・（）もここに含まれるので半角化される）
   */
  function normalizeStatus(v) {
    return String(v == null ? '' : v)
      .replace(/[！-～]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); })
      .replace(/[\s　]/g, '')
      .toUpperCase();
  }

  /** 重複判定キー：会社名＋担当名（姓） */
  function rowKey(company, lastName) {
    const c = companyKey(company);
    if (!c) return '';
    return c + '|' + normalizeName(lastName);
  }

  /** 会社名だけのキー（法人格を落として比較する） */
  function companyKey(company) {
    return normalizeName(company)
      .replace(/(株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|㈱|㈲|株|有)/g, '');
  }

  function normalizeName(v) {
    return String(v == null ? '' : v)
      .replace(/[！-～]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); })
      .replace(/[\s　\t]/g, '')
      .replace(/[()（）・.,、。･｢｣「」\-ー―‐_/\\]/g, '')
      .toLowerCase();
  }

  /** Date / 「2026/08/21」/「8/21」などを Date にする。判定できなければ null */
  function parseDateValue(v) {
    if (v instanceof Date && !isNaN(v.getTime())) return new Date(v.getFullYear(), v.getMonth(), v.getDate());
    const s = String(v == null ? '' : v).trim()
      .replace(/[！-～]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); });
    if (!s) return null;

    let m = s.match(/^(\d{4})[\/\-年](\d{1,2})[\/\-月](\d{1,2})/);
    if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));

    m = s.match(/^(\d{1,2})[\/\-月](\d{1,2})/);
    if (m) return new Date(new Date().getFullYear(), Number(m[1]) - 1, Number(m[2])); // 年が無い表記は今年

    return null;
  }

  function toCell(v) {
    if (v instanceof Date) return v;
    return v == null ? '' : v;
  }

  function sameCell(a, b) {
    if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime();
    if (a instanceof Date || b instanceof Date) return false;
    return String(a == null ? '' : a) === String(b == null ? '' : b);
  }

  function isBlankRow(row) {
    return !String(row[CONFIG.SRC.COL_COMPANY - 1] || '').trim();
  }

  /** 列番号 → 列名（1 → A、28 → AB） */
  function colLetter(col) {
    let s = '';
    let n = col;
    while (n > 0) {
      const r = (n - 1) % 26;
      s = String.fromCharCode(65 + r) + s;
      n = Math.floor((n - 1) / 26);
    }
    return s;
  }

  function errText(err) {
    return err && err.message ? err.message : String(err);
  }

  // ----- コンテキスト構築 -------------------------------------------

  function buildContext() {
    const srcSs = SpreadsheetApp.openById(CONFIG.SRC.ID);
    const dstSs = SpreadsheetApp.openById(CONFIG.DST.ID);
    const srcSheet = getSheet(srcSs, CONFIG.SRC.SHEET);
    const dstSheet = getSheet(dstSs, CONFIG.DST.SHEET);

    const srcHeaderRow = findHeaderRow(srcSheet, CONFIG.SRC.COL_STATUS);
    const dstHeaderRow = findHeaderRow(dstSheet, CONFIG.DST.COL_STATUS);

    ensureLinkHeader(srcSheet, srcHeaderRow, CONFIG.SRC.COL_LINK);
    ensureLinkHeader(dstSheet, dstHeaderRow, CONFIG.DST.COL_LINK);

    const srcLast = srcSheet.getLastRow();
    const srcWidth = Math.max(srcSheet.getLastColumn(), CONFIG.SRC.COL_LINK);
    const srcRows = srcLast > srcHeaderRow
      ? srcSheet.getRange(srcHeaderRow + 1, 1, srcLast - srcHeaderRow, srcWidth).getValues()
      : [];

    const dstLast = dstSheet.getLastRow();
    const dstWidth = Math.max(dstSheet.getLastColumn(), CONFIG.DST.COL_LINK);
    const dstRows = dstLast > dstHeaderRow
      ? dstSheet.getRange(dstHeaderRow + 1, 1, dstLast - dstHeaderRow, dstWidth).getValues()
      : [];

    const dstByLink = {};
    const dstByKey = {};
    const ownerSeen = {};
    const dstOwners = [];
    let maxNo = 0;

    dstRows.forEach(function (r, i) {
      const entry = { rowNo: dstHeaderRow + 1 + i };
      const link = String(r[CONFIG.DST.COL_LINK - 1] || '').trim();
      if (link) {
        dstByLink[link] = entry;
        maxNo = Math.max(maxNo, linkNoToInt(link));
      }
      const key = rowKey(r[CONFIG.DST.COL_COMPANY - 1], r[CONFIG.DST.COL_LAST_NAME - 1]);
      if (key && !dstByKey[key]) dstByKey[key] = entry; // 同一キーが複数あれば上の行を優先

      const owner = String(r[CONFIG.DST.COL_OWNER - 1] || '').trim();
      if (owner && !ownerSeen[owner]) { ownerSeen[owner] = true; dstOwners.push(owner); }
    });

    srcRows.forEach(function (r) {
      maxNo = Math.max(maxNo, linkNoToInt(String(r[CONFIG.SRC.COL_LINK - 1] || '').trim()));
    });

    const counter = { value: maxNo };
    return {
      srcSheet: srcSheet, dstSheet: dstSheet,
      srcHeaderRow: srcHeaderRow, dstHeaderRow: dstHeaderRow,
      srcRows: srcRows,
      dstByLink: dstByLink, dstByKey: dstByKey, dstOwners: dstOwners,
      logBuffer: [],
      verbose: false,
      nextLinkNo: function () {
        counter.value += 1;
        return CONFIG.LINK_PREFIX + ('0000' + counter.value).slice(-4);
      },
    };
  }

  function getSheet(ss, name) {
    const target = normalizeName(name);
    const hit = ss.getSheets().filter(function (s) { return normalizeName(s.getName()) === target; })[0];
    if (!hit) throw new Error('シートが見つかりません： ' + name);
    return hit;
  }

  /** 先頭5行のうち、指定列が「ステータス」になっている行を見出し行とみなす */
  function findHeaderRow(sheet, statusCol) {
    const rows = Math.min(5, sheet.getLastRow());
    if (rows < 1) return 1;
    const values = sheet.getRange(1, statusCol, rows, 1).getValues();
    for (let i = 0; i < values.length; i++) {
      if (normalizeName(values[i][0]) === normalizeName('ステータス')) return i + 1;
    }
    return 1;
  }

  function ensureLinkHeader(sheet, headerRow, col) {
    const cell = sheet.getRange(headerRow, col);
    if (!String(cell.getValue() || '').trim()) cell.setValue(CONFIG.LINK_HEADER);
  }

  function linkNoToInt(link) {
    const m = String(link || '').match(/(\d+)\s*$/);
    return m ? Number(m[1]) : 0;
  }

  // ----- ログ -------------------------------------------------------

  function addLog(ctx, type, linkNo, company, status, detail) {
    ctx.logBuffer.push([new Date(), type, linkNo, company, status, detail]);
  }

  function flushLog(ctx) {
    if (!ctx.logBuffer.length) return;
    const ss = SpreadsheetApp.openById(CONFIG.SRC.ID);
    let sheet = ss.getSheetByName(CONFIG.LOG_SHEET);
    if (!sheet) {
      sheet = ss.insertSheet(CONFIG.LOG_SHEET);
      sheet.appendRow(['日時', '種別', '転記No', '会社名', 'ステータス', '詳細']);
      sheet.setFrozenRows(1);
      sheet.hideSheet();
    }
    sheet.getRange(sheet.getLastRow() + 1, 1, ctx.logBuffer.length, 6).setValues(ctx.logBuffer);
    ctx.logBuffer = [];

    const over = sheet.getLastRow() - 1 - CONFIG.LOG_MAX_ROWS;
    if (over > 0) sheet.deleteRows(2, over);
  }

  // ----- 診断 -------------------------------------------------------

  /**
   * うまく転記されないときに実行する。会社名の一部を渡すと、
   * 何が起きているかを Apps Script の「実行ログ」に出す。
   */
  function debug(companyKeyword) {
    const out = [];
    const say = function (s) { out.push(s); Logger.log(s); };

    say('■ コード版： ' + AYOMI_VERSION);

    const triggers = ScriptApp.getProjectTriggers()
      .filter(function (t) { return t.getHandlerFunction().indexOf('AYOMI_') === 0; })
      .map(function (t) { return t.getHandlerFunction() + '（' + t.getEventType() + '）'; });
    say('■ このスクリプトのトリガー： ' + (triggers.length ? triggers.join(' / ') : '**なし → AYOMI_setupTriggers を実行**'));
    say('■ 同期する列（転記先）： ' + CONFIG.SYNC_MAP.map(function (m) { return colLetter(m.dst); }).join(','));

    const ctx = buildContext();
    say('■ ヨミ表： 見出し' + ctx.srcHeaderRow + '行 / データ' + ctx.srcRows.length + '行');
    say('■ Ａヨミ案件： 見出し' + ctx.dstHeaderRow + '行 / 担当営業の呼び名 ' + ctx.dstOwners.join(','));

    if (!companyKeyword) {
      say('※ 会社名の一部を引数に渡すと、その案件の状態を調べます');
      return out.join('\n');
    }

    const needle = normalizeName(companyKeyword);
    let found = 0;

    for (let i = 0; i < ctx.srcRows.length; i++) {
      const row = ctx.srcRows[i];
      const company = String(row[CONFIG.SRC.COL_COMPANY - 1] || '');
      if (normalizeName(company).indexOf(needle) < 0) continue;
      found++;

      const rowNo = ctx.srcHeaderRow + 1 + i;
      const link = String(row[CONFIG.SRC.COL_LINK - 1] || '').trim();
      const status = row[CONFIG.SRC.COL_STATUS - 1];

      say('');
      say('--- ヨミ表 ' + rowNo + '行： ' + company);
      say('　P列 ステータス： 「' + status + '」 → 書き込む値「' + mapStatus(status) + '」');
      say('　R列 初回商談日： 「' + row[CONFIG.SRC.COL_FIRST_MTG - 1] + '」 → 対象期間内？ ' + (isTargetRow(row) ? 'はい' : 'いいえ'));
      say('　BB列 転記No： 「' + (link || '（空）') + '」');

      if (!link) { say('　→ 未転記。' + (isTargetRow(row) ? '次の同期で転記されるはず' : '条件を満たしていないので転記されない')); continue; }
      if (link === CONFIG.EXCLUDE_MARK) { say('　→ 対象外なので同期しない'); continue; }

      const hit = ctx.dstByLink[link];
      if (!hit) { say('　→ **Ａヨミ案件に同じ転記Noの行が見つからない**（BB列を確認）'); continue; }

      const width = Math.max(ctx.dstSheet.getLastColumn(), CONFIG.DST.COL_LINK);
      const dstRow = ctx.dstSheet.getRange(hit.rowNo, 1, 1, width).getValues()[0];
      say('　→ Ａヨミ案件 ' + hit.rowNo + '行と紐づいている（現在のP列： 「' + dstRow[CONFIG.DST.COL_STATUS - 1] + '」）');

      syncBlocks().forEach(function (b) {
        const allowed = allowedValues(ctx.dstSheet, hit.rowNo, b.start, b.cols.length);
        b.cols.forEach(function (srcCol, k) {
          const dstCol = b.start + k;
          let v = srcValue(row, srcCol);
          if (dstCol === CONFIG.DST.COL_OWNER) {
            const named = resolveOwner(ctx, dstCol, v, allowed[k]);
            if (named !== null) v = named;
          }
          const cur = dstRow[dstCol - 1];
          if (sameCell(cur, v)) return;
          if (rejectedBy(allowed[k], v)) {
            say('　　' + colLetter(dstCol) + '： 「' + cur + '」→「' + v + '」 **入力規則に無いので書けない**（候補： ' + allowed[k].join('/') + '）');
          } else {
            say('　　' + colLetter(dstCol) + '： 「' + cur + '」→「' + v + '」 書き込み対象');
          }
        });
      });

      say('　→ この行だけ実際に同期してみます…');
      say('　→ 結果： ' + JSON.stringify(sync([rowNo])));
    }

    if (!found) say('※ ヨミ表に「' + companyKeyword + '」を含む会社名が見つかりませんでした');
    return out.join('\n');
  }

  return {
    initialLink: initialLink,
    relinkByCompany: relinkByCompany,
    setupStatusDropdown: setupStatusDropdown,
    setupTriggers: setupTriggers,
    onEdit: onEdit,
    syncAll: syncAll,
    debug: debug,
  };
})();
