/**
 * ヨミ表（スプレッドシート1）→ Ａヨミ案件（スプレッドシート2）同期
 *
 * ・ヨミ表のP列ステータスが「Aヨミ*」または「受注」になったら、Ａヨミ案件へ行を転記する
 * ・転記済みの行は、その後のヨミ表側の変更（ステータス・商談日など）をＡヨミ案件に上書き反映する
 * ・Ａヨミ案件のステータスは「受注 / Aヨミ / 失注」の3つだけに丸める
 * ・同期は ヨミ表 → Ａヨミ案件 の一方向のみ
 *
 * 設置先：スプレッドシート1（ヨミ表があるファイル）の Apps Script
 * 初回手順：setupStatusDropdown() → initialLinkExisting() → setupTriggers() を1回ずつ実行
 */

// ===== 設定 =========================================================

const CONFIG = {
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
    COL_LINK: 54,      // BB Aヨミリスト転記（このスクリプトが使う。無ければ自動でヘッダーを作る）
  },

  // 対象にする初回商談日の下限（この日以降の案件だけ同期する）
  TARGET_FROM: new Date(2026, 6, 20), // 2026/07/20

  LINK_PREFIX: 'A-',    // 転記ナンバーの接頭辞（A-0001 …）
  EXCLUDE_MARK: '対象外', // BB列にこの文字が入っている行は同期しない
  LINK_HEADER: 'Aヨミリスト転記',
  LOG_SHEET: '_同期ログ',
  LOG_MAX_ROWS: 5000,

  /**
   * Ａヨミ案件のP列に入れてよい値。これ以外は書き込まない。
   * setupStatusDropdown() を実行すると、この3つだけのプルダウンに張り替える。
   */
  DST_STATUS_VALUES: ['受注', 'Aヨミ', '失注'],

  /**
   * ステータスの言い換え：ヨミ表の値 → Ａヨミ案件に書き込む値。
   * ヨミ表は「Aヨミ（決裁者口頭合意済）」「Cヨミ（長期）」など細かいが、
   * Ａヨミ案件は「受注 / Aヨミ / 失注」の3つだけに丸める。
   * 上から順に判定し、最初に当たったものを使う（全角半角・空白を吸収した後の値で判定）。
   */
  STATUS_MAP: [
    { when: /^Aヨミ/, to: 'Aヨミ' }, // Aヨミ（決裁者口頭合意済）/（決裁者確認中）など
    { when: /^受注$/, to: '受注' },  // 「受注後キャンセル」は下の失注に落ちる
    { when: /^/,      to: '失注' },  // 上記以外（Cヨミ・リスケ・追客NG・受注後キャンセル…）はすべて失注
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
   * Z期間 / AA合計金額 は、ヨミ表に対応する列が無い（または単位が違う）ため touch しない。
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

// ===== 手動で1回だけ実行する関数 =====================================

/**
 * 【初回セットアップ 1/2】
 * いま既にヨミ表にあるAヨミ／受注の行を「今後変わった分だけ同期」の状態に整える。
 *   ・Ａヨミ案件に同じ案件（会社名＋姓）が既にある → 転記ナンバーを両方に振って紐づけるだけ（内容は上書きしない）
 *   ・Ａヨミ案件に無い                          → BB列に「対象外」を入れて、一括流し込みを防ぐ
 * あとから転記したくなったら、その行のBB列を空にすれば次回の同期で転記される。
 */
function initialLinkExisting() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30 * 1000)) throw new Error('他の同期処理が実行中です。少し待って再実行してください。');
  try {
    const ctx = buildContext_();
    let linked = 0, excluded = 0, skipped = 0;

    for (let i = 0; i < ctx.srcRows.length; i++) {
      const row = ctx.srcRows[i];
      const rowNo = ctx.srcHeaderRow + 1 + i;
      if (isBlankRow_(row)) continue;

      const link = String(row[CONFIG.SRC.COL_LINK - 1] || '').trim();
      if (link) { skipped++; continue; } // 既に番号や「対象外」が入っている行は触らない
      if (!isTargetRow_(row)) { skipped++; continue; }

      const key = rowKey_(row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_LAST_NAME - 1]);
      const hit = key ? ctx.dstByKey[key] : null;

      try {
        if (hit) {
          const no = ctx.nextLinkNo();
          ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
          ctx.dstSheet.getRange(hit.rowNo, CONFIG.DST.COL_LINK).setValue(no);
          ctx.dstByLink[no] = hit;
          linked++;
          log_(ctx, 'リンク', no, row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
               'ヨミ表' + rowNo + '行 ↔ Ａヨミ案件' + hit.rowNo + '行');
        } else {
          ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(CONFIG.EXCLUDE_MARK);
          excluded++;
          log_(ctx, '対象外', '', row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
               '初期セットアップ時の既存行のため転記しない（ヨミ表' + rowNo + '行）');
        }
      } catch (err) {
        // 1行のエラーで全体を止めない
        log_(ctx, 'エラー', '', row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
             'ヨミ表' + rowNo + '行： ' + (err && err.message ? err.message : err));
      }
    }
    flushLog_(ctx);
    const msg = '初期セットアップ完了： 紐づけ ' + linked + '件 ／ 対象外 ' + excluded + '件 ／ 変更なし ' + skipped + '件';
    Logger.log(msg);
    return msg;
  } finally {
    lock.releaseLock();
  }
}

/**
 * 【任意・手動】「対象外」になった行を、会社名だけで突合し直して紐づける。
 *
 * initialLinkExisting() は 会社名＋担当名（姓）で突合するので、ヨミ表とＡヨミ案件で
 * 姓の入れ方が違う（片方が「横田」、片方が「横田徳」など）と一致せず「対象外」になる。
 * この関数は会社名だけで照合し、Ａヨミ案件にまだ番号が無い行が見つかれば紐づける。
 */
function relinkExcludedByCompany() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30 * 1000)) throw new Error('他の同期処理が実行中です。少し待って再実行してください。');
  try {
    const ctx = buildContext_();
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
      const c = companyKey_(r[CONFIG.DST.COL_COMPANY - 1]);
      if (c && !freeByCompany[c]) freeByCompany[c] = { rowNo: ctx.dstHeaderRow + 1 + i };
    });

    for (let i = 0; i < ctx.srcRows.length; i++) {
      const row = ctx.srcRows[i];
      const rowNo = ctx.srcHeaderRow + 1 + i;
      if (String(row[CONFIG.SRC.COL_LINK - 1] || '').trim() !== CONFIG.EXCLUDE_MARK) continue;

      const c = companyKey_(row[CONFIG.SRC.COL_COMPANY - 1]);
      const hit = c ? freeByCompany[c] : null;
      if (!hit) continue;

      const no = ctx.nextLinkNo();
      ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
      ctx.dstSheet.getRange(hit.rowNo, CONFIG.DST.COL_LINK).setValue(no);
      delete freeByCompany[c];
      linked++;
      log_(ctx, 'リンク', no, row[CONFIG.SRC.COL_COMPANY - 1], row[CONFIG.SRC.COL_STATUS - 1],
           '会社名で再突合（ヨミ表' + rowNo + '行 ↔ Ａヨミ案件' + hit.rowNo + '行）');
    }
    flushLog_(ctx);
    const msg = '会社名での再突合： ' + linked + '件を紐づけました';
    Logger.log(msg);
    return msg;
  } finally {
    lock.releaseLock();
  }
}

/**
 * 【任意・手動】Ａヨミ案件のP列（ステータス）のプルダウンを「受注 / Aヨミ / 失注」の3つに固定する。
 * この3つ以外は手入力でも選べなくなる。列を作り替えたくなったら CONFIG.DST_STATUS_VALUES を変えて再実行。
 */
function setupStatusDropdown() {
  const dstSheet = getSheet_(SpreadsheetApp.openById(CONFIG.DST.ID), CONFIG.DST.SHEET);
  const headerRow = findHeaderRow_(dstSheet, CONFIG.DST.COL_STATUS);
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
 * 【初回セットアップ 2/2】
 * 編集時トリガー（即時）と1時間ごとの差分チェックを設置する。二重登録はしない。
 */
function setupTriggers() {
  const wanted = ['onEditYomi', 'syncAll'];
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (wanted.indexOf(t.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(t);
  });
  const ss = SpreadsheetApp.openById(CONFIG.SRC.ID);
  ScriptApp.newTrigger('onEditYomi').forSpreadsheet(ss).onEdit().create();
  ScriptApp.newTrigger('syncAll').timeBased().everyHours(1).create();
  const msg = 'トリガーを設置しました（編集時：即時 ／ 時間主導：1時間ごと）';
  Logger.log(msg);
  return msg;
}

// ===== トリガーから呼ばれる関数 =====================================

/** 編集時トリガー：ヨミ表の編集行だけを同期する */
function onEditYomi(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (normalizeName_(sheet.getName()) !== normalizeName_(CONFIG.SRC.SHEET)) return;

  // 同期のきっかけになる列（ステータス／初回商談日／転記ナンバー）が含まれていなければ何もしない
  const c1 = e.range.getColumn();
  const c2 = c1 + e.range.getNumColumns() - 1;
  const watch = [CONFIG.SRC.COL_STATUS, CONFIG.SRC.COL_FIRST_MTG, CONFIG.SRC.COL_LINK];
  if (!watch.some(function (c) { return c >= c1 && c <= c2; })) return;

  const rows = [];
  for (let r = e.range.getRow(); r < e.range.getRow() + e.range.getNumRows(); r++) rows.push(r);
  runSync_(rows);
}

/** 時間主導トリガー：ヨミ表の全行をチェックする（編集トリガーの取りこぼし対策） */
function syncAll() {
  return runSync_(null);
}

// ===== 同期本体 =====================================================

/**
 * @param {Array<number>|null} onlyRows 対象にするヨミ表の行番号。null なら全行。
 */
function runSync_(onlyRows) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30 * 1000)) {
    Logger.log('同期スキップ：他の処理が実行中');
    return;
  }
  try {
    const ctx = buildContext_();
    ctx.verbose = !!onlyRows; // 編集起点のときだけ「見送り」もログに残す（時間主導では毎時同じ行を書かない）
    const result = { added: 0, updated: 0, skipped: 0, error: 0 };

    for (let i = 0; i < ctx.srcRows.length; i++) {
      const rowNo = ctx.srcHeaderRow + 1 + i;
      if (onlyRows && onlyRows.indexOf(rowNo) < 0) continue;
      try {
        syncOneRow_(ctx, ctx.srcRows[i], rowNo, result);
      } catch (err) {
        result.error++;
        log_(ctx, 'エラー', '', ctx.srcRows[i][CONFIG.SRC.COL_COMPANY - 1], '',
             'ヨミ表' + rowNo + '行： ' + (err && err.message ? err.message : err));
      }
    }
    flushLog_(ctx);
    Logger.log('同期完了： 新規 %s / 更新 %s / 対象外 %s / エラー %s',
               result.added, result.updated, result.skipped, result.error);
    return result;
  } finally {
    lock.releaseLock();
  }
}

function syncOneRow_(ctx, row, rowNo, result) {
  if (isBlankRow_(row)) { result.skipped++; return; }

  const linkRaw = String(row[CONFIG.SRC.COL_LINK - 1] || '').trim();
  if (linkRaw === CONFIG.EXCLUDE_MARK) { result.skipped++; return; }

  const company = row[CONFIG.SRC.COL_COMPANY - 1];
  const status = row[CONFIG.SRC.COL_STATUS - 1];

  // --- 既に転記済みの行：ステータスが何に変わっても追随して上書きする ---
  if (linkRaw) {
    let hit = ctx.dstByLink[linkRaw];
    if (!hit) {
      // Ａヨミ案件側の行が消えている等 → 同じ番号で作り直す
      hit = appendDstRow_(ctx, row, linkRaw);
      log_(ctx, '再作成', linkRaw, company, status, 'Ａヨミ案件に該当行が無かったため再追加');
      result.added++;
      return;
    }
    if (writeDstRow_(ctx, hit.rowNo, row)) {
      result.updated++;
      log_(ctx, '更新', linkRaw, company, status, 'Ａヨミ案件' + hit.rowNo + '行を上書き');
    } else {
      result.skipped++;
    }
    return;
  }

  // --- 未転記の行：Aヨミ／受注になっていて、初回商談日が対象期間内なら転記する ---
  if (!isTargetRow_(row)) {
    result.skipped++;
    if (ctx.verbose && isTargetStatus_(status)) {
      const d = parseDate_(row[CONFIG.SRC.COL_FIRST_MTG - 1]);
      log_(ctx, '見送り', '', company, status,
           'ヨミ表' + rowNo + '行： 初回商談日（R列）が ' +
           (d ? Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy/MM/dd') + ' で対象期間外' : '未入力または日付として読めない'));
    }
    return;
  }

  const key = rowKey_(company, row[CONFIG.SRC.COL_LAST_NAME - 1]);
  const existing = key ? ctx.dstByKey[key] : null;
  const no = ctx.nextLinkNo();

  if (existing) {
    // 手入力で既にＡヨミ案件にある案件 → 重複させず、その行に紐づけて更新
    ctx.dstSheet.getRange(existing.rowNo, CONFIG.DST.COL_LINK).setValue(no);
    ctx.dstByLink[no] = existing;
    writeDstRow_(ctx, existing.rowNo, row, true);
    ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
    result.updated++;
    log_(ctx, '紐づけ更新', no, company, status,
         '既存行と一致（Ａヨミ案件' + existing.rowNo + '行）のため新規追加せず更新');
    return;
  }

  const added = appendDstRow_(ctx, row, no);
  ctx.srcSheet.getRange(rowNo, CONFIG.SRC.COL_LINK).setValue(no);
  result.added++;
  log_(ctx, '新規', no, company, status, 'Ａヨミ案件' + added.rowNo + '行に追加');
}

/**
 * マッピング対象の転記先列を、連続した列のかたまり（ブロック）にまとめる。
 * マッピングに無い列（Ａヨミ案件の手入力列や数式列）には一切触れないようにするため。
 * 例： [1..17], [18..20], [28..29]
 */
function syncBlocks_() {
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
function appendDstRow_(ctx, srcRow, linkNo) {
  const rowNo = Math.max(ctx.dstSheet.getLastRow(), ctx.dstHeaderRow) + 1;
  writeDstRow_(ctx, rowNo, srcRow, true);
  ctx.dstSheet.getRange(rowNo, CONFIG.DST.COL_LINK).setValue(linkNo);

  const entry = { rowNo: rowNo };
  ctx.dstByLink[linkNo] = entry;
  const key = rowKey_(srcRow[CONFIG.SRC.COL_COMPANY - 1], srcRow[CONFIG.SRC.COL_LAST_NAME - 1]);
  if (key && !ctx.dstByKey[key]) ctx.dstByKey[key] = entry;
  return entry;
}

/**
 * Ａヨミ案件の1行を、マッピング対象の列だけ書き込む。変更があれば true。
 *
 * Ａヨミ案件の列にはプルダウン（データの入力規則）が設定されており、候補に無い値を
 * 書こうとすると setValues ごと例外になる。そうなると同じ塊にある他の列（ステータス等）まで
 * 巻き添えで書き込まれないので、**弾かれるセルだけ事前に外して**書き込む。
 *
 * @param {boolean} logSkips 弾かれたセルをログに残すか（毎時の全件チェックでは同じ行が
 *                           何度もログに積もるので、新規転記時と編集起点のときだけ true）
 */
function writeDstRow_(ctx, dstRowNo, srcRow, logSkips) {
  const company = srcRow[CONFIG.SRC.COL_COMPANY - 1];
  const noteSkip = logSkips || ctx.verbose;
  let changed = false;

  syncBlocks_().forEach(function (b) {
    const range = ctx.dstSheet.getRange(dstRowNo, b.start, 1, b.cols.length);
    const current = range.getValues()[0];
    const allowed = allowedValues_(ctx.dstSheet, dstRowNo, b.start, b.cols.length);
    const next = current.slice();
    let blockChanged = false;

    b.cols.forEach(function (srcCol, i) {
      let v = srcValue_(srcRow, srcCol);
      if (b.start + i === CONFIG.DST.COL_OWNER) {
        const named = resolveOwner_(ctx, b.start + i, v, allowed[i]);
        if (named !== null) v = named;
      }
      if (sameCell_(current[i], v)) return;
      if (rejectedBy_(allowed[i], v)) {
        const fixed = resolveOwner_(ctx, b.start + i, v, allowed[i]);
        if (fixed !== null) { next[i] = fixed; blockChanged = true; return; }
        if (noteSkip) {
          log_(ctx, 'スキップ', '', company, '',
               'Ａヨミ案件 ' + colLetter_(b.start + i) + dstRowNo + ' は入力規則にない値なので書き込まなかった： 「' +
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
        if (sameCell_(current[i], next[i])) return;
        try {
          ctx.dstSheet.getRange(dstRowNo, b.start + i).setValue(next[i]);
          changed = true;
        } catch (e2) {
          if (noteSkip) {
            log_(ctx, 'スキップ', '', company, '',
                 'Ａヨミ案件 ' + colLetter_(b.start + i) + dstRowNo + ' が入力規則で拒否された： 「' + next[i] + '」');
          }
        }
      });
    }
  });
  return changed;
}

/**
 * 担当営業を、Ａヨミ案件で使われている呼び名に寄せる。
 * ヨミ表「土屋慧介」→ Ａヨミ案件「土屋」、ヨミ表「菅野敬彦」→ Ａヨミ案件「敬彦」のように、
 * 「候補がヨミ表の氏名に含まれる（または逆）」で1つに絞れたらその候補を使う。
 * 絞れなければ null を返し、呼び出し側でスキップ扱いにする。
 *
 * @param {Array<string>|null} allowed その列のプルダウン候補（無ければ null）
 * @return {string|null}
 */
function resolveOwner_(ctx, dstCol, value, allowed) {
  if (dstCol !== CONFIG.DST.COL_OWNER) return null;
  const raw = String(value == null ? '' : value).trim();
  if (!raw) return null;

  if (CONFIG.OWNER_MAP[raw]) return CONFIG.OWNER_MAP[raw];

  // プルダウンがあればその候補、無ければＡヨミ案件で既に使われている呼び名から探す
  const candidates = (allowed && allowed.length) ? allowed : ctx.dstOwners;
  if (!candidates || !candidates.length) return null;

  const v = normalizeName_(raw);
  const hits = candidates.filter(function (c) {
    const n = normalizeName_(c);
    return n && (v.indexOf(n) >= 0 || n.indexOf(v) >= 0);
  });
  return hits.length === 1 ? hits[0] : null; // 複数に当たったら曖昧なので書かない
}

/** ヨミ表の値を、Ａヨミ案件に書き込む値に変換する（ステータスだけ言い換える） */
function srcValue_(srcRow, srcCol) {
  const raw = toCell_(srcRow[srcCol - 1]);
  return srcCol === CONFIG.SRC.COL_STATUS ? mapStatus_(raw) : raw;
}

/**
 * ヨミ表のステータスを、Ａヨミ案件の「受注 / Aヨミ / 失注」に丸める。
 * ヨミ表が空欄のときは空欄のまま（勝手に失注にしない）。
 */
function mapStatus_(v) {
  const s = normalizeStatus_(v);
  if (!s) return '';
  for (let i = 0; i < CONFIG.STATUS_MAP.length; i++) {
    if (CONFIG.STATUS_MAP[i].when.test(s)) return CONFIG.STATUS_MAP[i].to;
  }
  return toCell_(v);
}

/** 各セルの「入力規則で許可された値」の配列。制限が無い（または警告のみ）の列は null */
function allowedValues_(sheet, rowNo, startCol, numCols) {
  let dvs;
  try {
    dvs = sheet.getRange(rowNo, startCol, 1, numCols).getDataValidations()[0];
  } catch (e) {
    return new Array(numCols).fill(null);
  }
  return dvs.map(function (dv) { return dvAllowed_(dv); });
}

function dvAllowed_(dv) {
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
function rejectedBy_(allowed, value) {
  if (!allowed || !allowed.length) return false;
  if (value instanceof Date) return false; // 日付のリスト規則は想定しない
  const s = String(value == null ? '' : value).trim();
  if (!s) return false;                    // 空欄は規則に関わらず書ける
  return allowed.indexOf(s) < 0;
}

/** 列番号 → 列名（1 → A、28 → AB） */
function colLetter_(col) {
  let s = '';
  let n = col;
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

// ===== 判定・ユーティリティ =========================================

/** 転記のきっかけになるステータスか（Aヨミ or 受注） */
function isTargetStatus_(v) {
  const s = normalizeStatus_(v);
  return s.indexOf('Aヨミ') === 0 // 「Aヨミ（決裁者口頭合意済）」等も拾う
      || s === '受注';            // 「受注後キャンセル」は拾わない
}

/** 未転記の行を転記対象とみなすか（Aヨミ or 受注 かつ 初回商談日が対象期間内） */
function isTargetRow_(row) {
  if (!isTargetStatus_(row[CONFIG.SRC.COL_STATUS - 1])) return false;

  const d = parseDate_(row[CONFIG.SRC.COL_FIRST_MTG - 1]);
  if (!d) return false;
  return d.getTime() >= CONFIG.TARGET_FROM.getTime();
}

/**
 * ステータスの表記ゆれを吸収する。
 * 「Ａヨミ」「a ヨミ」「Ａヨミ（決裁者口頭合意済）」→ いずれも「Aヨミ…」になる。
 * （！-～ は全角ASCIIの範囲。Ａ-Ｚ・（）もここに含まれるので半角化される）
 */
function normalizeStatus_(v) {
  return String(v == null ? '' : v)
    .replace(/[！-～]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); })
    .replace(/[\s　]/g, '')
    .toUpperCase();
}

/** 重複判定キー：会社名＋担当名（姓）。法人格・記号・空白の表記ゆれを吸収する */
function rowKey_(company, lastName) {
  const c = companyKey_(company);
  if (!c) return '';
  return c + '|' + normalizeName_(lastName);
}

/** 会社名だけのキー（法人格を落として比較する） */
function companyKey_(company) {
  return normalizeName_(company)
    .replace(/(株式会社|有限会社|合同会社|合資会社|合名会社|一般社団法人|一般財団法人|㈱|㈲|株|有)/g, '');
}

function normalizeName_(v) {
  return String(v == null ? '' : v)
    .replace(/[！-～]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); })
    .replace(/[\s　\t]/g, '')
    .replace(/[()（）・.,、。･｢｣「」\-ー―‐_/\\]/g, '')
    .toLowerCase();
}

/** Date / 「2026/08/21」/「8/21」などを Date にする。判定できなければ null */
function parseDate_(v) {
  if (v instanceof Date && !isNaN(v.getTime())) return new Date(v.getFullYear(), v.getMonth(), v.getDate());
  const s = String(v == null ? '' : v).trim()
    .replace(/[！-～]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); });
  if (!s) return null;

  let m = s.match(/^(\d{4})[\/\-年](\d{1,2})[\/\-月](\d{1,2})/);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));

  m = s.match(/^(\d{1,2})[\/\-月](\d{1,2})/);
  if (m) {
    // 年が無い表記は「今年」とみなす
    const y = new Date().getFullYear();
    return new Date(y, Number(m[1]) - 1, Number(m[2]));
  }
  return null;
}

function toCell_(v) {
  if (v instanceof Date) return v;
  return v == null ? '' : v;
}

function sameCell_(a, b) {
  if (a instanceof Date && b instanceof Date) return a.getTime() === b.getTime();
  if (a instanceof Date || b instanceof Date) return false;
  return String(a == null ? '' : a) === String(b == null ? '' : b);
}

function isBlankRow_(row) {
  return !String(row[CONFIG.SRC.COL_COMPANY - 1] || '').trim();
}

// ===== コンテキスト構築 =============================================

function buildContext_() {
  const srcSs = SpreadsheetApp.openById(CONFIG.SRC.ID);
  const dstSs = SpreadsheetApp.openById(CONFIG.DST.ID);
  const srcSheet = getSheet_(srcSs, CONFIG.SRC.SHEET);
  const dstSheet = getSheet_(dstSs, CONFIG.DST.SHEET);

  const srcHeaderRow = findHeaderRow_(srcSheet, CONFIG.SRC.COL_STATUS);
  const dstHeaderRow = findHeaderRow_(dstSheet, CONFIG.DST.COL_STATUS);

  ensureLinkHeader_(srcSheet, srcHeaderRow, CONFIG.SRC.COL_LINK);
  ensureLinkHeader_(dstSheet, dstHeaderRow, CONFIG.DST.COL_LINK);

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
    const rowNo = dstHeaderRow + 1 + i;
    const entry = { rowNo: rowNo };
    const link = String(r[CONFIG.DST.COL_LINK - 1] || '').trim();
    if (link) {
      dstByLink[link] = entry;
      maxNo = Math.max(maxNo, linkNoToInt_(link));
    }
    const key = rowKey_(r[CONFIG.DST.COL_COMPANY - 1], r[CONFIG.DST.COL_LAST_NAME - 1]);
    if (key && !dstByKey[key]) dstByKey[key] = entry; // 同一キーが複数あれば上の行を優先

    const owner = String(r[CONFIG.DST.COL_OWNER - 1] || '').trim();
    if (owner && !ownerSeen[owner]) { ownerSeen[owner] = true; dstOwners.push(owner); }
  });

  srcRows.forEach(function (r) {
    maxNo = Math.max(maxNo, linkNoToInt_(String(r[CONFIG.SRC.COL_LINK - 1] || '').trim()));
  });

  const counter = { value: maxNo };
  return {
    srcSheet: srcSheet, dstSheet: dstSheet,
    srcHeaderRow: srcHeaderRow, dstHeaderRow: dstHeaderRow,
    srcRows: srcRows,
    dstByLink: dstByLink, dstByKey: dstByKey,
    dstOwners: dstOwners,
    logBuffer: [],
    verbose: false,
    nextLinkNo: function () {
      counter.value += 1;
      return CONFIG.LINK_PREFIX + ('0000' + counter.value).slice(-4);
    },
  };
}

function getSheet_(ss, name) {
  const target = normalizeName_(name);
  const hit = ss.getSheets().filter(function (s) { return normalizeName_(s.getName()) === target; })[0];
  if (!hit) throw new Error('シートが見つかりません： ' + name);
  return hit;
}

/** 先頭5行のうち、指定列が「ステータス」になっている行を見出し行とみなす */
function findHeaderRow_(sheet, statusCol) {
  const rows = Math.min(5, sheet.getLastRow());
  if (rows < 1) return 1;
  const values = sheet.getRange(1, statusCol, rows, 1).getValues();
  for (let i = 0; i < values.length; i++) {
    if (normalizeName_(values[i][0]) === normalizeName_('ステータス')) return i + 1;
  }
  return 1;
}

function ensureLinkHeader_(sheet, headerRow, col) {
  const cell = sheet.getRange(headerRow, col);
  if (!String(cell.getValue() || '').trim()) cell.setValue(CONFIG.LINK_HEADER);
}

function linkNoToInt_(link) {
  const m = String(link || '').match(/(\d+)\s*$/);
  return m ? Number(m[1]) : 0;
}

// ===== ログ =========================================================

function log_(ctx, type, linkNo, company, status, detail) {
  ctx.logBuffer.push([new Date(), type, linkNo, company, status, detail]);
}

function flushLog_(ctx) {
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
