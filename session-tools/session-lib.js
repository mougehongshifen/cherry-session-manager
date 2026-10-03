// Cherry Studio 会话复制 - 共享库
// 供 duplicate-session.js / pick.js / shortcuts.js 复用
import { Database } from "bun:sqlite";
import { randomBytes } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, rmSync, readdirSync, statSync, readFileSync, writeFileSync, renameSync } from "node:fs";
import { join, dirname, resolve, basename, sep } from "node:path";
import { homedir } from "node:os";

export const ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

// ==========================================================
// 定位 Cherry Studio 的数据目录
//
// 注意：这里以前写死了作者的绝对路径，导致换台电脑完全不可用。
// 现在按下面的顺序自动查找：
//   1) 显式传入（--data-dir 参数 / 函数参数）
//   2) 环境变量 CHERRY_DATA_DIR
//   3) ~/.cherrystudio/boot-config.json —— Cherry 自己记的 userData
//   4) ~/.cherrystudio/config/config.json —— 旧版 Cherry 记的位置
//   5) 各平台默认的 userData 位置
//
// 注意：真正放数据库的是 <userData>/Data/，不是 <userData>/ 本身。
// ==========================================================
export const DB_NAME = "cherrystudio.sqlite";
const CHERRY_HOME = join(homedir(), ".cherrystudio");

function readJson(p) {
  try { return JSON.parse(readFileSync(p, "utf8")); } catch { return null; }
}

/** Cherry 自己声明过的 userData 目录，可执行文件存在的排前面。 */
function declaredUserData() {
  const out = [];
  const boot = readJson(join(CHERRY_HOME, "boot-config.json"));
  const map = boot && boot["app.user_data_path"];
  if (map && typeof map === "object") {
    for (const [exe, ud] of Object.entries(map)) {
      if (typeof ud === "string" && ud) out.push({ ud, rank: existsSync(exe) ? 0 : 1 });
    }
  }
  const legacy = readJson(join(CHERRY_HOME, "config", "config.json"));
  if (legacy && Array.isArray(legacy.appDataPath)) {
    for (const e of legacy.appDataPath) {
      if (e && typeof e.dataPath === "string" && e.dataPath) out.push({ ud: e.dataPath, rank: 2 });
    }
  }
  return out.sort((a, b) => a.rank - b.rank).map((x) => x.ud);
}

/** 各平台上 Cherry Studio 默认的 userData 位置。 */
function platformDefaultUserData() {
  const home = homedir();
  const out = [];
  if (process.platform === "win32") {
    const roaming = process.env.APPDATA || join(home, "AppData", "Roaming");
    const local = process.env.LOCALAPPDATA || join(home, "AppData", "Local");
    for (const b of [roaming, local]) {
      out.push(join(b, "CherryStudio"), join(b, "Cherry Studio"));
    }
  } else if (process.platform === "darwin") {
    out.push(join(home, "Library", "Application Support", "CherryStudio"));
  } else {
    out.push(join(process.env.XDG_CONFIG_HOME || join(home, ".config"), "CherryStudio"));
  }
  return out;
}

/** 解析数据目录（直接含 cherrystudio.sqlite 的那一层）。
 *
 *  显式指定（参数或 CHERRY_DATA_DIR）时**以它为准**：无效就抛错，
 *  绝不悄悄换成自动探测到的另一个目录 —— 否则用户以为在用 A，实际动的是 B。
 *  没有显式指定时才自动探测，探测不到返回 null。
 */
export function resolveDataDir(explicit) {
  const direct = explicit || process.env.CHERRY_DATA_DIR;
  if (direct) {
    if (existsSync(join(direct, DB_NAME))) return direct;
    throw new Error(
      "指定的数据目录里没有 " + DB_NAME + "：\n  " + direct + "\n" +
      "  请确认这确实是 Cherry Studio 的数据目录（里面应当同时有 " +
      DB_NAME + " 和 Agents 子目录）。"
    );
  }

  const tries = [];
  const push = (d) => { if (d && !tries.includes(d)) tries.push(d); };
  for (const ud of declaredUserData()) { push(join(ud, "Data")); push(ud); }
  for (const ud of platformDefaultUserData()) { push(join(ud, "Data")); push(ud); }
  for (const d of tries) { if (existsSync(join(d, DB_NAME))) return d; }
  return null;
}

/** 解析数据目录，找不到就抛一个说得清楚的错。 */
export function requireDataDir(explicit) {
  const d = resolveDataDir(explicit);
  if (d) return d;
  throw new Error(
    "找不到 Cherry Studio 的数据目录（里面应当有 " + DB_NAME + "）。\n" +
    "  · 用 --data-dir <目录> 指定，或设置环境变量 CHERRY_DATA_DIR\n" +
    "  · 也可以用 GUI（Cherry 会话管理器）里的「设置 → 选择数据目录」"
  );
}

export function dbPathOf(dataDir) { return join(requireDataDir(dataDir), DB_NAME); }
export function dshRootOf(dataDir) { return join(requireDataDir(dataDir), "Agents", ".dsh", "sessions"); }
export function claudeProjectsOf(dataDir) { return join(requireDataDir(dataDir), "Agents", ".claude", "projects"); }

export function openDb(readonly = false, dataDir) {
  return new Database(dbPathOf(dataDir), readonly ? { readonly: true } : { readwrite: true });
}

/** 生成一个在 cur 和 nxt 之间排序的新 order_key（nxt 为 null 表示追加到最后）。
 *  当两者之间确实没有合法键时（例如邻居被反复插入占满），会在 cur 上加中值字符扩展，
 *  保证结果严格大于 cur 即可（可能等于 nxt，排序上仍稳定）。 */
export function orderKeyBetween(cur, nxt) {
  const L = ALPHABET.length;
  const mid = ALPHABET[Math.floor(L / 2)];
  if (!cur) throw new Error("order_key 不能为空");
  try {
    const k = orderKeyRaw(cur, nxt);
    if (k > cur && (!nxt || k < nxt)) return k;
    if (k > cur) return k;
  } catch { /* 落到下面的扩展策略 */ }
  // 扩展：不断在 cur 后补中值字符，直到严格大于 cur
  let key = cur + mid;
  let guard = 0;
  while (!(key > cur) && guard++ < 200) key += mid;
  return key;
}

function orderKeyRaw(cur, nxt) {
  const L = ALPHABET.length;
  let i = 0;
  for (let guard = 0; guard < 500; guard++) {
    const ci = i < cur.length ? ALPHABET.indexOf(cur[i]) : 0;
    const ni = nxt === null ? L : i < nxt.length ? ALPHABET.indexOf(nxt[i]) : L;
    if (ci + 1 < ni) return cur.slice(0, i) + ALPHABET[ci + 1];
    if (ci + 1 === ni) {
      if (i + 1 >= cur.length) return cur + ALPHABET[Math.floor(L / 2)];
      i++;
      continue;
    }
    if (ci === ni) {
      if (i + 1 >= cur.length) return cur + ALPHABET[Math.floor(L / 2)];
      i++;
      continue;
    }
    throw new Error(`无法在 ${cur} 与 ${nxt} 之间生成 order_key`);
  }
  throw new Error(`order_key 生成超出循环上限: ${cur} / ${nxt}`);
}

/** UUID v7 形态（时间有序），与 Cherry 自己生成的 id 风格一致 */
export function uuidV7Like() {
  const bytes = randomBytes(16);
  const ms = BigInt(Date.now());
  bytes[0] = Number((ms >> 40n) & 0xffn);
  bytes[1] = Number((ms >> 32n) & 0xffn);
  bytes[2] = Number((ms >> 24n) & 0xffn);
  bytes[3] = Number((ms >> 16n) & 0xffn);
  bytes[4] = Number((ms >> 8n) & 0xffn);
  bytes[5] = Number(ms & 0xffn);
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function findDshSessionDirs(sessionId, dataDir) {
  const hits = [];
  const root = dshRootOf(dataDir);
  if (!existsSync(root)) return hits;
  for (const proj of readdirSync(root)) {
    const p = join(root, proj, sessionId);
    if (existsSync(p) && statSync(p).isDirectory()) hits.push(p);
  }
  return hits;
}

/** 改写 DSH 会话日志头部的会话 id。
 *  该文件是「多个 zstd frame 顺序拼接」的流，且 DSH 读取时要求第 1 个 frame
 *  单独解压后恰好是一行头信息。所以只重写 frame0，其余 frame 原样保留。 */
export function rewriteSessionLog(srcFile, dstFile, oldId, newId) {
  const raw = readFileSync(srcFile);
  const magic = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
  const starts = [];
  let i = raw.indexOf(magic, 0);
  while (i !== -1) { starts.push(i); i = raw.indexOf(magic, i + 1); }
  if (starts.length === 0) throw new Error("没找到 zstd frame: " + srcFile);
  const frames = starts.map((s, k) => raw.subarray(s, k + 1 < starts.length ? starts[k + 1] : raw.length));

  const headText = new TextDecoder().decode(Bun.zstdDecompressSync(new Uint8Array(frames[0])));
  if (headText.split("\n").length - 1 !== 1) throw new Error("日志第 1 个 frame 不是单独一行，结构不符合预期");
  const head = JSON.parse(headText);
  if (head.type !== "session") throw new Error("日志头部不是 session 记录: " + headText.slice(0, 120));
  if (head.id !== oldId) throw new Error(`日志头部 id (${head.id}) 与源会话 (${oldId}) 不一致`);

  head.id = newId;
  const newFrame0 = Bun.zstdCompressSync(new TextEncoder().encode(JSON.stringify(head) + "\n"));
  const parts = [new Uint8Array(newFrame0), ...frames.slice(1).map(f => new Uint8Array(f))];
  let total = 0; for (const p of parts) total += p.length;
  const out = new Uint8Array(total);
  let off = 0; for (const p of parts) { out.set(p, off); off += p.length; }
  writeFileSync(dstFile, out);
  return frames.length;
}

/** 列出会话（按创建时间倒序），带 agent 名和消息数 */
export function listSessions(db, { agentType = null, limit = null } = {}) {
  let sql = `
    SELECT s.id, s.name, s.order_key, s.agent_id, s.workspace_id, s.created_at, s.last_activity_at,
           a.name AS agent_name, a.type AS agent_type,
           (SELECT COUNT(*) FROM agent_session_message m WHERE m.session_id = s.id) AS msg_count
    FROM agent_session s LEFT JOIN agent a ON a.id = s.agent_id`;
  const args = [];
  if (agentType) { sql += " WHERE a.type = ?"; args.push(agentType); }
  sql += " ORDER BY s.created_at DESC";
  if (limit) { sql += " LIMIT ?"; args.push(limit); }
  return db.query(sql).all(...args);
}

/** 按名字或 id 找唯一会话；找不到返回 null，名字撞车会抛错 */
export function resolveSession(db, target) {
  if (!target) return null;
  const byId = db.query("SELECT * FROM agent_session WHERE id = ?").get(target);
  if (byId) return byId;
  const byName = db.query("SELECT * FROM agent_session WHERE name = ?").all(target);
  if (byName.length === 1) return byName[0];
  if (byName.length > 1) {
    throw new Error(`有 ${byName.length} 个会话都叫「${target}」，请改用会话 id：\n` +
      byName.map(s => "  " + s.id + "  " + new Date(s.created_at).toLocaleString()).join("\n"));
  }
  return null;
}

/** 备份数据库。
 *  重要：这个库跑在 WAL 模式下，新写入可能还在 -wal 里没落盘。
 *  只拷主文件会拿到「上一次 checkpoint」的旧状态（踩过这个坑：备份里查不到刚建的会话）。
 *  这里用 VACUUM INTO 生成一份一致单文件快照，不依赖 WAL 状态。 */
export function backupDb(dataDir) {
  const db = dbPathOf(dataDir);
  const path = db + ".bak-sessioncopy-" +
    new Date().toISOString().replace(/[:.]/g, "-");
  try {
    const conn = openDb(true, dataDir);
    // VACUUM INTO 需要目标文件不存在
    if (existsSync(path)) rmSync(path, { force: true });
    conn.run(`VACUUM INTO '${path.replace(/'/g, "''")}'`);
    conn.close();
    return path;
  } catch (e) {
    // 退回文件级复制：连 -wal / -shm 一起拷，至少不丢 WAL 里的数据
    copyFileSync(db, path);
    if (existsSync(db + "-wal")) copyFileSync(db + "-wal", path + "-wal");
    if (existsSync(db + "-shm")) copyFileSync(db + "-shm", path + "-shm");
    return path;
  }
}

/**
 * 复制一个会话，返回 { newId, newName, msgCount, orderKey, dshDir }
 * 流程：先复制 DSH 运行时历史（失败就不动数据库），再写数据库（失败就回滚文件）。
 *
 * backup: 默认 false —— 不再每次都整库快照（一份 ≈130 MB，堆起来好几个 GB）。
 *         写入本身是一个事务，失败会整体回滚；真要额外保险就传 backup: true。
 */
export function duplicateSession({ sessionId, sessionName, newName, silent = false, dataDir, backup = false }) {
  const log = silent ? () => {} : (...a) => console.log(...a);
  const dataDirUsed = requireDataDir(dataDir);
  const db = openDb(false, dataDirUsed);
  const src = resolveSession(db, sessionId || sessionName);
  if (!src) throw new Error("找不到会话: " + (sessionId || sessionName));

  // 不复制失败/投递型的中间消息，只留正常对话
  const messages = db.query(
    `SELECT * FROM agent_session_message
     WHERE session_id = ? AND status != 'error' AND delivery_sender_session_id IS NULL
     ORDER BY created_at, id`
  ).all(src.id);

  const newId = uuidV7Like();
  const finalName = newName || `${src.name} (副本)`;
  const now = Date.now();

  const next = db.query(
    "SELECT order_key FROM agent_session WHERE order_key > ? ORDER BY order_key LIMIT 1"
  ).get(src.order_key);
  const newOrderKey = orderKeyBetween(src.order_key, next ? next.order_key : null);
  // 只要严格大于源会话的键，排序就是稳定的（极端情况下允许等于下一个键）
  if (!(newOrderKey > src.order_key)) {
    throw new Error(`order_key 不合法: 期望 > ${src.order_key}，实际 ${newOrderKey}`);
  }

  const backupPath = backup ? backupDb(dataDirUsed) : null;

  // 1) 先复制 DSH 运行时历史
  const dshDirs = findDshSessionDirs(src.id, dataDirUsed);
  const copiedDirs = [];
  try {
    for (const d of dshDirs) {
      const dst = join(dirname(d), newId);
      mkdirSync(dst, { recursive: true });
      for (const f of readdirSync(d)) {
        const srcFile = join(d, f);
        const dstFile = join(dst, f);
        if (f.endsWith(".zstd") && /session\.jsonl/.test(f)) {
          const n = rewriteSessionLog(srcFile, dstFile, src.id, newId);
          log(`  会话日志 header id: ${src.id} -> ${newId} (frame 数 ${n})`);
        } else {
          copyFileSync(srcFile, dstFile);
        }
      }
      copiedDirs.push(dst);
    }

    // 2) 再写数据库
    const insertMessage = db.query(`
      INSERT INTO agent_session_message
        (id, session_id, role, data, searchable_text, status, model_id, message_snapshot,
         stats, runtime_resume_token, delivery, delivery_status, delivery_turn_ref,
         delivery_in_reply_to, delivery_sender_session_id, fts_rowid, created_at, updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)
    `);
    const tsShift = 1;
    db.transaction(() => {
      db.run(`
        INSERT INTO agent_session
          (id, agent_id, name, is_name_manually_edited, description, workspace_id, task_schedule_id,
           trace_id, order_key, last_activity_at, created_at, updated_at)
        VALUES (?,?,?,0,?,?,NULL,?,?,?,?,?)
      `, [newId, src.agent_id, finalName, src.description ?? "", src.workspace_id,
          src.trace_id ?? "", newOrderKey, now, now, now]);
      for (const m of messages) {
        insertMessage.run(
          uuidV7Like(), newId, m.role, m.data, "", m.status ?? "success",
          m.model_id ?? null, m.message_snapshot ?? null, m.stats ?? null,
          newId, null, null, null, null, null,
          m.created_at + tsShift, m.updated_at + tsShift
        );
      }
    })();
  } catch (e) {
    // 回滚：删掉已写入的 dsh 目录、删掉可能已写入的库记录
    for (const d of copiedDirs) rmSync(d, { recursive: true, force: true });
    try { db.run("DELETE FROM agent_session_message WHERE session_id = ?", [newId]); } catch {}
    try { db.run("DELETE FROM agent_session WHERE id = ?", [newId]); } catch {}
    db.close();
    throw e;
  }
  db.close();

  return {
    newId, newName: finalName, msgCount: messages.length, orderKey: newOrderKey,
    sourceName: src.name, sourceId: src.id, backup: backupPath, dshDir: copiedDirs[0] ?? null,
  };
}

/** 名字里带这些记号就被当成「复制出来的副本」（界面上的「清理副本」用它筛选）。 */
export const COPY_MARKERS = ["(副本)", "（副本）", "(copy)", "(Copy)", "(COPY)"];

export function isCopyName(name) {
  const n = String(name ?? "");
  return COPY_MARKERS.some((m) => n.includes(m));
}

/** 把入参整理成干净、去重的 id 列表（接受数组、单个字符串、逗号分隔的字符串）。 */
function normalizeIds(ids) {
  const raw = Array.isArray(ids) ? ids : [ids];
  const list = raw
    .flatMap((x) => String(x ?? "").split(","))
    .map((s) => s.trim())
    .filter(Boolean);
  if (!list.length) throw new Error("没有指定要处理的会话 id");
  const uniq = [...new Set(list)];
  if (uniq.length > 2000) throw new Error(`一次最多处理 2000 个会话（当前 ${uniq.length} 个）`);
  return uniq;
}

/** 只读地问一句「删掉这些会话会发生什么」，供界面确认框用。不写入任何东西。 */
export function inspectForDelete({ ids, dataDir }) {
  const list = normalizeIds(ids);
  const dataDirUsed = requireDataDir(dataDir);
  const db = openDb(true, dataDirUsed);
  try {
    const one = db.query("SELECT id, name, created_at FROM agent_session WHERE id = ?");
    const cnt = db.query("SELECT COUNT(*) c FROM agent_session_message WHERE session_id = ?");
    return list.map((id) => {
      const s = one.get(id);
      return {
        id,
        exists: !!s,
        name: s ? (s.name ?? "") : null,
        createdAt: s ? s.created_at : null,
        msgCount: s ? cnt.get(id).c : 0,
        dshDirs: s ? findDshSessionDirs(id, dataDirUsed) : [],
      };
    });
  } finally {
    db.close();
  }
}

// ==========================================================
// 删除会话
//
// ⚠️ 删掉的会话只能从备份里找回来（`--undo` 是给刚复制出来的副本用的，
//    不是通用的时光机）。
//
// ⚠️ 为什么这里要手动删一堆「依赖行」，而不是指望外键的
//    ON DELETE CASCADE / SET NULL：
//    bun:sqlite 新建的连接实测 `PRAGMA foreign_keys = 0`（关的），
//    所以那些级联规则**根本不会触发**，会留下一堆悬空引用
//    （比如置顶列表里点开一个已经不存在的会话）。
//    这里按 PRAGMA foreign_key_list 读出来的真实关系逐个显式处理，
//    外键开或关，结果都一样：
//      agent_session_message.session_id          → 该会话的消息
//      agent_session_message_file_ref.source_id  → 消息引用的附件（原为 CASCADE）
//      agent_channel_session.session_id          → IM 渠道路由（原为 CASCADE）
//      agent_channel.session_id                  → IM 渠道绑定的当前会话（原为 SET NULL）
//      pin / entity_tag (entity_type='session')  → 置顶、标签
//    全文索引不用管：agent_session_message 上的 AFTER DELETE 触发器会自己清理。
// ==========================================================

/** 数一数这个会话挂了多少「依赖行」（全是带条件的索引查询）。 */
function countDependents(db, id) {
  const g = (sql, params = [id]) => {
    try {
      const r = db.query(sql).get(...params);
      return r ? r.c : 0;
    } catch {
      return 0; // 老版本数据库可能没有这张表
    }
  };
  return {
    messages: g("SELECT COUNT(*) c FROM agent_session_message WHERE session_id = ?"),
    fileRefs: g("SELECT COUNT(*) c FROM agent_session_message_file_ref WHERE source_id IN "
              + "(SELECT id FROM agent_session_message WHERE session_id = ?)"),
    pins: g("SELECT COUNT(*) c FROM pin WHERE entity_type = 'session' AND entity_id = ?"),
    tags: g("SELECT COUNT(*) c FROM entity_tag WHERE entity_type = 'session' AND entity_id = ?"),
    channelSessions: g("SELECT COUNT(*) c FROM agent_channel_session WHERE session_id = ?"),
    channels: g("SELECT COUNT(*) c FROM agent_channel WHERE session_id = ?"),
  };
}

/** 删除若干会话：会话本身 + 消息 + 相关引用 + DSH 运行时历史目录。
 *
 *  默认**不再**整库备份（那是以前的做法，一份 130 MB，堆起来好几个 GB）。
 *  改成先写一份「删除留底」：只装这次要删的那几行 + 把运行时目录改名收进去。
 *  留底写失败就直接抛错，数据库一行都不动。
 *  backup:true 仍然可以走老路（整库快照），给不放心的用户 / --with-backup 用。
 */
export function removeSessions({ ids, dataDir, backup = false, dryRun = false, journal = true }) {
  const list = normalizeIds(ids);
  const dataDirUsed = requireDataDir(dataDir);

  // 先只读地摸清楚要删什么：一个都不存在就直接报错，不必白写留底
  const plan = inspectForDelete({ ids: list, dataDir: dataDirUsed });
  const found = plan.filter((p) => p.exists);
  const missing = plan.filter((p) => !p.exists).map((p) => p.id);
  if (!found.length) {
    throw new Error("这些会话在数据库里都不存在（是不是已经删过了？）：\n  " + list.join("\n  "));
  }
  if (dryRun) {
    return { dryRun: true, backup: null, journal: null, removed: found, missing };
  }

  const db = openDb(false, dataDirUsed);
  let journalInfo = null;
  const details = [];
  try {
    // 1) 先留底（写文件；失败就在这里抛，数据库还没动过）
    if (journal) journalInfo = writeJournal(dataDirUsed, db, found);

    // 2) 再删数据库（一个事务；失败整批回滚）
    db.transaction(() => {
      for (const p of found) {
        const id = p.id;
        // ⚠️ 条数一律先 SELECT 数出来，不用 db.run() 返回的 changes：
        //    实测 bun:sqlite 在有 FTS 触发器的表上返回的 changes 不可信
        //    （删 3 条消息报成 11，删 1 行附件引用报成 0）。
        //    数错了只是「报告的数字不对」，但报告不实等于骗人，所以照实数。
        const counts = countDependents(db, id);
        db.run("DELETE FROM agent_session_message_file_ref WHERE source_id IN "
             + "(SELECT id FROM agent_session_message WHERE session_id = ?)", [id]);
        db.run("UPDATE agent_channel SET session_id = NULL WHERE session_id = ?", [id]);
        db.run("DELETE FROM agent_channel_session WHERE session_id = ?", [id]);
        db.run("DELETE FROM pin WHERE entity_type = 'session' AND entity_id = ?", [id]);
        try {
          db.run("DELETE FROM entity_tag WHERE entity_type = 'session' AND entity_id = ?", [id]);
        } catch { /* 老版本数据库没有 entity_tag 表 */ }
        db.run("DELETE FROM agent_session_message WHERE session_id = ?", [id]);
        db.run("DELETE FROM agent_session WHERE id = ?", [id]);
        // 删完确认一下真的没了；没删掉就让整个事务回滚，不许报「成功」
        const left = db.query("SELECT COUNT(*) c FROM agent_session WHERE id = ?").get(id).c;
        if (left) throw new Error(`数据库没有删掉会话 ${id}（已回滚，什么都没变）`);
        details.push({ ...p, ...counts });
      }
    })();
  } finally {
    db.close();
  }

  // 3) 运行时目录：改名收进留底（同卷 rename，不复制数据、不删文件）
  let stash = { done: [], failed: [] };
  if (journalInfo) {
    stash = stashDshDirs(found, journalInfo.dir);
  } else {
    for (const p of found) {
      for (const from of p.dshDirs) {
        try { rmSync(from, { recursive: true, force: true }); stash.done.push({ from, to: null }); }
        catch (e) { stash.failed.push({ from, error: String(e.message || e) }); }
      }
    }
  }
  for (const d of details) {
    d.dshDirsStashed = stash.done.filter((x) => x.to && d.dshDirs.includes(x.from)).map((x) => x.to);
    d.dshDirsRemoved = stash.done.filter((x) => !x.to && d.dshDirs.includes(x.from)).map((x) => x.from);
    d.dirErrors = stash.failed.filter((x) => d.dshDirs.includes(x.from))
      .map((x) => `${x.from}（没能收进留底：${x.error}）`);
  }
  if (journalInfo) {
    journalInfo.dshDirs = stash.done.length;
    journalInfo.dshKeepErrors = stash.failed;
    try {
      journalInfo.bytes = statSync(journalInfo.file).size
        + stash.done.reduce((a, x) => a + dirSizeRecursive(x.to), 0);
    } catch { /* 大小算不出来不影响功能 */ }
  }

  // 4) 可选：老式的整库快照（默认不做，留给 --with-backup / 设置里打开的用户）
  const backupPath = backup ? backupDb(dataDirUsed) : null;
  return { dryRun: false, backup: backupPath, journal: journalInfo, removed: details, missing };
}

/** 删除一个会话（兼容旧接口）。 */
export function removeSession({ sessionId, dataDir }) {
  const r = removeSessions({ ids: [sessionId], dataDir });
  const one = r.removed[0];
  return { name: one.name, id: one.id,
           dshDirs: one.dshDirsStashed ?? one.dshDirsRemoved ?? one.dshDirs,
           backup: r.backup, journal: r.journal, msgCount: one.messages };
}

/** 列出「复制出来的副本」（界面/命令行用它筛选），按创建时间倒序。 */
export function listCopies(db, { agentId = null } = {}) {
  const rows = listSessions(db, {});
  return rows.filter((r) =>
    isCopyName(r.name) && (agentId === null || r.agent_id === agentId));
}

/** 列出自动备份（新的在前）。
 *  只认「数据目录里、名字是 <库名>.bak-sessioncopy- 开头」的普通文件。 */
export function listBackups(dataDir) {
  const dir = requireDataDir(dataDir);
  const prefix = DB_NAME + ".bak-sessioncopy-";
  const out = [];
  for (const f of readdirSync(dir)) {
    if (!f.startsWith(prefix)) continue;
    const full = join(dir, f);
    let st;
    try { st = statSync(full); } catch { continue; }
    if (!st.isFile()) continue;
    out.push({ path: full, name: f, size: st.size, mtime: st.mtimeMs });
  }
  return out.sort((a, b) => b.mtime - a.mtime);
}

/** 只保留最新的 keep 份自动备份，其余删掉。
 *
 *  为什么要这个：每次写入都要备份一份**整个**数据库（一百多 MB），
 *  用久了会在数据目录里堆成好几个 GB —— 实测有人堆到 5.9 GB。
 *  最新的几份足够救命（它们包含最新的数据），更老的价值很低。
 *
 *  安全阀：只删「数据目录正下方、名字符合备份规则」的文件，别的路径一律不碰。 */
export function pruneBackups({ dataDir, keep = 5, dryRun = false }) {
  const dir = resolve(requireDataDir(dataDir));
  const all = listBackups(dataDir);
  const k = Math.max(0, Math.floor(Number(keep) || 0));
  const doomed = all.slice(k);
  let freed = 0;
  if (dryRun) {
    freed = doomed.reduce((a, b) => a + b.size, 0);
  } else {
    for (const b of doomed) {
      if (resolve(dirname(b.path)) !== dir) continue;
      if (!b.name.startsWith(DB_NAME + ".bak-sessioncopy-")) continue;
      rmSync(b.path, { force: true });
      freed += b.size;
    }
  }
  return { dryRun, deleted: doomed, kept: all.slice(0, k), freedBytes: freed, total: all.length };
}

// ==========================================================
// 删除留底（不是整库备份！）
//
// 以前每次写入都 `VACUUM INTO` 备份**一整个**数据库（一份 ≈ 130 MB），
// 用久了会堆成好几个 GB —— 而删除本来就是用户确认过的操作，凭什么还留一份全库？
// 现在改成：只在 <数据目录>\_deleted_sessions\<时间戳>\ 里放
//   · journal.json —— 被删的那些行的原样拷贝（几十 KB ~ 几 MB）
//   · dsh\<工作目录>\<会话id>\ —— 被删的运行时历史目录（改名搬进来，不占额外空间）
// 这样既能「说删就删」（数据库里立刻没了），万一工具自己写错了也还能还原。
// 用户随时可以在界面上清理这些留底。
// ==========================================================

export const JOURNAL_DIR = "_deleted_sessions";
const JOURNAL_KIND = "cherry-session-manager-delete-journal";

function journalRoot(dataDir) {
  return join(requireDataDir(dataDir), JOURNAL_DIR);
}

function stampName() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

/** JSON 里放不下二进制（BLOB 会变成 Uint8Array），就转成 base64 带标记存。 */
function revive(v) {
  if (v && typeof v === "object" && typeof v.__blob_b64 === "string") {
    return new Uint8Array(Buffer.from(v.__blob_b64, "base64"));
  }
  return v;
}

function blobSafe(_key, v) {
  if (v instanceof Uint8Array) return { __blob_b64: Buffer.from(v).toString("base64") };
  return v;
}

function queryOr(db, sql, params, dflt = []) {
  try { return db.query(sql).all(...params); } catch { return dflt; }
}

function journalDshTarget(journalDir, dshPath) {
  // <...>\.dsh\sessions\<工作目录>\<会话id>  ->  <留底>\dsh\<工作目录>\<会话id>
  return join(journalDir, "dsh", basename(dirname(dshPath)), basename(dshPath));
}

/** 把要删的行 + 运行时目录的**去向**先写进留底。写失败就抛错（此时还没动数据库）。 */
function writeJournal(dataDir, db, found) {
  const dir = join(journalRoot(dataDir), `${stampName()}-${found.length}sessions`);
  mkdirSync(dir, { recursive: true });
  const sessions = [];
  for (const p of found) {
    sessions.push({
      id: p.id,
      name: p.name,
      session: db.query("SELECT * FROM agent_session WHERE id = ?").get(p.id),
      messages: queryOr(db, "SELECT * FROM agent_session_message WHERE session_id = ?", [p.id]),
      // 消息引用的附件也要留：不然还原回来的会话会「有消息、没附件」
      fileRefs: queryOr(db, "SELECT * FROM agent_session_message_file_ref WHERE source_id IN "
                          + "(SELECT id FROM agent_session_message WHERE session_id = ?)", [p.id]),
      pins: queryOr(db, "SELECT * FROM pin WHERE entity_type = 'session' AND entity_id = ?", [p.id]),
      tags: queryOr(db, "SELECT * FROM entity_tag WHERE entity_type = 'session' AND entity_id = ?", [p.id]),
      channelSessions: queryOr(db, "SELECT * FROM agent_channel_session WHERE session_id = ?", [p.id]),
      channels: queryOr(db, "SELECT id, session_id FROM agent_channel WHERE session_id = ?", [p.id]),
      dshDirs: p.dshDirs.map((from) => ({ from, to: journalDshTarget(dir, from) })),
    });
  }
  const payload = {
    kind: JOURNAL_KIND,
    version: 1,
    deletedAt: Date.now(),
    dataDir,
    sessions,
  };
  const file = join(dir, "journal.json");
  writeFileSync(file, JSON.stringify(payload, blobSafe));
  return { dir, file, count: sessions.length, bytes: statSync(file).size };
}

/** 把运行时目录改名搬进留底（同盘 rename 是瞬时的，不复制数据）。 */
function stashDshDirs(found, journalDir) {
  const done = [];
  const failed = [];
  for (const p of found) {
    for (const from of p.dshDirs) {
      const to = journalDshTarget(journalDir, from);
      try {
        mkdirSync(dirname(to), { recursive: true });
        renameSync(from, to);          // 同卷改名，不复制
        done.push({ from, to });
      } catch (e) {
        // 搬不动就留着不动（宁可留个无害的孤儿目录，也不能偷偷删掉）
        failed.push({ from, error: String(e.message || e) });
      }
    }
  }
  return { done, failed };
}

function dirSizeRecursive(p) {
  let total = 0;
  let st;
  try { st = statSync(p); } catch { return 0; }
  if (st.isFile()) return st.size;
  let names = [];
  try { names = readdirSync(p); } catch { return 0; }
  for (const n of names) total += dirSizeRecursive(join(p, n));
  return total;
}

/** 列出所有删除留底（新的在前）。 */
export function listDeletedJournals(dataDir) {
  const root = journalRoot(dataDir);
  if (!existsSync(root)) return [];
  const out = [];
  for (const name of readdirSync(root)) {
    const dir = join(root, name);
    const file = join(dir, "journal.json");
    if (!existsSync(file)) continue;
    let j = null;
    try { j = JSON.parse(readFileSync(file, "utf8")); } catch { /* 坏文件也列出来，让人能删 */ }
    out.push({
      dir, file, name,
      size: dirSizeRecursive(dir),
      mtime: (j && j.deletedAt) || statSync(file).mtimeMs,
      sessions: ((j && j.sessions) || []).map((s) => ({
        id: s.id, name: s.name, messages: (s.messages || []).length,
      })),
    });
  }
  return out.sort((a, b) => b.mtime - a.mtime);
}

/** 只保留最新的 keep 份留底，其余删掉。 */
export function pruneDeleted({ dataDir, keep = 5, dryRun = false }) {
  const root = resolve(journalRoot(dataDir));
  const all = listDeletedJournals(dataDir);
  const k = Math.max(0, Math.floor(Number(keep) || 0));
  const doomed = all.slice(k);
  let freed = 0;
  if (dryRun) {
    freed = doomed.reduce((a, b) => a + b.size, 0);
  } else {
    for (const j of doomed) {
      if (resolve(dirname(j.dir)) !== root) continue;   // 安全阀：只删留底目录底下的
      rmSync(j.dir, { recursive: true, force: true });
      freed += j.size;
    }
  }
  return { dryRun, deleted: doomed, kept: all.slice(0, k), freedBytes: freed, total: all.length };
}

function tableColumns(db, table) {
  return db.query(`PRAGMA table_info("${table}")`).all().map((r) => r.name);
}

/** 把行插回去：只插「当前表结构里确实存在的列」（Cherry 改过表结构也不会炸）。 */
function insertRows(db, table, rows) {
  if (!rows || !rows.length) return 0;
  let cols;
  try { cols = tableColumns(db, table); } catch { return 0; }
  let n = 0;
  for (const row of rows) {
    const keys = Object.keys(row).filter((k) => cols.includes(k) && row[k] !== undefined);
    if (!keys.length) continue;
    const sql = `INSERT INTO "${table}" (${keys.map((k) => `"${k}"`).join(",")}) `
              + `VALUES (${keys.map(() => "?").join(",")})`;
    db.run(sql, keys.map((k) => revive(row[k])));
    n++;
  }
  return n;
}

/** 从一份删除留底里把会话还原回数据库（id 被占了就跳过并说明）。 */
export function restoreDeleted({ file, sessionIds = null, dataDir }) {
  const dir = requireDataDir(dataDir);
  const journalPath = resolve(file);
  // 安全阀：只认留底目录里的 journal.json
  const jroot = resolve(journalRoot(dataDir));
  if (!journalPath.startsWith(jroot + sep)) {
    throw new Error("这个文件不在删除留底目录里，拒绝还原：\n  " + journalPath);
  }
  const j = JSON.parse(readFileSync(journalPath, "utf8"));
  if (j.kind !== JOURNAL_KIND) throw new Error("这不是本工具产生的删除留底：" + journalPath);
  const want = sessionIds
    ? new Set((Array.isArray(sessionIds) ? sessionIds : String(sessionIds).split(",")).map((s) => String(s).trim()).filter(Boolean))
    : null;

  const db = openDb(false, dir);
  const out = { restored: [], skipped: [], dirsRestored: 0, dirsMissing: [], journal: journalPath,
                fkNew: 0 };
  const fkCount = () => {
    try { return db.query("PRAGMA foreign_key_check").all().length; } catch { return 0; }
  };
  let fkBefore = 0;
  try {
    fkBefore = fkCount();
    db.transaction(() => {
      for (const s of j.sessions) {
        if (want && !want.has(s.id)) continue;
        if (db.query("SELECT id FROM agent_session WHERE id = ?").get(s.id)) {
          out.skipped.push({ id: s.id, name: s.name, why: "数据库里已经有同一个 id 的会话" });
          continue;
        }
        const nSession = insertRows(db, "agent_session", [s.session]);
        const nMsg = insertRows(db, "agent_session_message", s.messages);
        const nRef = insertRows(db, "agent_session_message_file_ref", s.fileRefs);
        const nPin = insertRows(db, "pin", s.pins);
        const nTag = insertRows(db, "entity_tag", s.tags);
        const nChan = insertRows(db, "agent_channel_session", s.channelSessions);
        for (const ch of s.channels || []) {
          try {
            db.run("UPDATE agent_channel SET session_id = ? WHERE id = ? AND session_id IS NULL",
                   [s.id, ch.id]);
          } catch { /* 老库没有这张表也无所谓 */ }
        }
        out.restored.push({ id: s.id, name: s.name, sessionRows: nSession, messages: nMsg,
                            fileRefs: nRef, pins: nPin, tags: nTag, channelSessions: nChan });
      }
    })();
    // 还原完看一眼有没有多出悬空引用：留底里的某一行，它的父行可能这期间已经被删了
    out.fkNew = Math.max(0, fkCount() - fkBefore);
  } finally {
    db.close();
  }

  // 运行时目录搬回原位（文件系统没有事务，放最后）
  const restoredIds = new Set(out.restored.map((r) => r.id));
  for (const s of j.sessions || []) {
    if (!restoredIds.has(s.id)) continue;
    for (const d of s.dshDirs || []) {
      if (!existsSync(d.to)) { out.dirsMissing.push(d.to); continue; }
      if (existsSync(d.from)) { out.dirsMissing.push(d.from + "（原位已经有东西了，没动）"); continue; }
      try {
        mkdirSync(dirname(d.from), { recursive: true });
        renameSync(d.to, d.from);
        out.dirsRestored++;
      } catch (e) {
        out.dirsMissing.push(`${d.from}（搬回去失败：${e.message}）`);
      }
    }
  }
  return out;
}

/** 命令行工具：取 --flag value */
export function argOf(argv, flag, def = null) {
  const i = argv.indexOf(flag);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : def;
}
