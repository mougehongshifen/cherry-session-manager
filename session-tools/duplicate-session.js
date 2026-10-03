// Cherry Studio 会话复制 / 分支工具（命令行版）
// 用法:
//   bun duplicate-session.js --session <sessionId 或会话名> [--name "新名字"] [--dry-run]
//   bun duplicate-session.js --undo <新会话id>            （删掉刚复制出来的那份，等价于 --delete）
//   bun duplicate-session.js --delete <id 或会话名> [--dry-run]
//   bun duplicate-session.js --delete-many <id,id,...> [--dry-run]
//   bun duplicate-session.js --list [--agent dsh]
//   bun duplicate-session.js --list-copies [--agent dsh]
//   bun duplicate-session.js --list-deleted
//   bun duplicate-session.js --restore-deleted <留底里的 journal.json> [--session <id>]
//   bun duplicate-session.js --prune-deleted [--keep 5] [--dry-run]
//   bun duplicate-session.js --backup
//   bun duplicate-session.js --list-backups
//   bun duplicate-session.js --prune-backups [--keep 5] [--dry-run]
//
// 数据目录默认自动查找（见 session-lib.js），也可以显式指定：
//   --data-dir "D:\CherryStudio\Data"     或设置环境变量 CHERRY_DATA_DIR
//
// 想点鼠标就用同目录的 pick.js（会话复制中心），或者图形界面的「Cherry 会话管理器」
// （右键会话 → 删除此会话；右键 Agent → 清理副本）。
//
// ⚠️ --delete / --delete-many 是**真删**（会话 + 消息 + 相关引用 + DSH 运行时历史目录）。
//    默认不做整库备份（一份 130 MB，堆起来好几个 GB），而是写一份很小的「删除留底」
//    （只装这次删掉的那几行，运行时目录是改名搬进去的）—— 要还原用 --restore-deleted。
//    想要老式的整库快照就加 --with-backup；不想留底就加 --no-journal。
//    不确定会删掉什么就先加 --dry-run。
import {
  openDb, listSessions, resolveSession, duplicateSession, removeSession, removeSessions,
  inspectForDelete, isCopyName, findDshSessionDirs, argOf, backupDb, resolveDataDir, dbPathOf,
  listBackups, pruneBackups, listDeletedJournals, pruneDeleted, restoreDeleted,
} from "./session-lib.js";

const argv = process.argv.slice(2);
const dryRun = argv.includes("--dry-run");
const undoId = argOf(argv, "--undo");
const deleteArg = argOf(argv, "--delete");
const deleteManyArg = argOf(argv, "--delete-many");
const target = argOf(argv, "--session");
const newNameArg = argOf(argv, "--name");
const dataDir = argOf(argv, "--data-dir");

// 出错时只打一行人话，不要甩一堆堆栈给用户
process.on("uncaughtException", (e) => {
  const m = String((e && e.message) || e);
  console.error("❌ " + m.split("\n").join("\n   "));
  process.exit(1);
});

if (argv.includes("--list-data-dir")) {
  // 调试用：只打印识别到的数据目录
  try {
    console.log(resolveDataDir(dataDir) || "(未找到)");
  } catch (e) {
    console.log("(无效) " + String(e.message || e).split("\n")[0]);
  }
  process.exit(0);
}

if (argv.includes("--backup")) {
  console.log("数据库已备份（一致快照，含 WAL 里最新写入）:");
  console.log("  " + backupDb(dataDir));
  process.exit(0);
}

function humanSize(n) {
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(0) + " KB";
  return n + " B";
}

if (argv.includes("--list-backups")) {
  const all = listBackups(dataDir);
  if (!all.length) { console.log("(没有自动备份)"); process.exit(0); }
  const total = all.reduce((a, b) => a + b.size, 0);
  console.log(`自动备份 ${all.length} 份，共 ${humanSize(total)}（新的在前）：`);
  for (const b of all) {
    console.log(`  ${new Date(b.mtime).toLocaleString()}  ${humanSize(b.size).padStart(9)}  ${b.name}`);
  }
  process.exit(0);
}

if (argv.includes("--prune-backups")) {
  const keep = Number(argOf(argv, "--keep", "5"));
  const r = pruneBackups({ dataDir, keep, dryRun });
  console.log(`自动备份共 ${r.total} 份，保留最新的 ${r.kept.length} 份。`);
  if (!r.deleted.length) { console.log("没有需要删的。"); process.exit(0); }
  if (r.dryRun) {
    console.log(`--dry-run：将会删除下面 ${r.deleted.length} 份（共 ${humanSize(r.freedBytes)}），什么都没改：`);
  } else {
    console.log(`已删除 ${r.deleted.length} 份旧备份，释放 ${humanSize(r.freedBytes)}：`);
  }
  for (const b of r.deleted) {
    console.log(`  ${new Date(b.mtime).toLocaleString()}  ${humanSize(b.size).padStart(9)}  ${b.name}`);
  }
  process.exit(0);
}

if (argv.includes("--list")) {
  const db = openDb(true, dataDir);
  const rows = listSessions(db, { agentType: argOf(argv, "--agent"), limit: Number(argOf(argv, "--limit", "0")) || null });
  db.close();
  for (const r of rows) {
    console.log([r.name, r.agent_name, r.msg_count + "条", r.id].join("  |  "));
  }
  process.exit(0);
}

if (argv.includes("--list-copies")) {
  const db = openDb(true, dataDir);
  const rows = listSessions(db, { agentType: argOf(argv, "--agent") }).filter((r) => isCopyName(r.name));
  db.close();
  if (!rows.length) console.log("(没有名字里带「副本」的会话)");
  for (const r of rows) {
    console.log([r.name, r.agent_name, r.msg_count + "条", r.id].join("  |  "));
  }
  process.exit(0);
}

// ---- 删除 ----

function printDeleteResult(r) {
  if (r.dryRun) {
    console.log("\n--dry-run：下面是「将会被删除」的东西，什么都没改。");
    for (const d of r.removed) {
      console.log(`  · ${d.name}  (${d.id})`);
      console.log(`      消息 ${d.msgCount} 条`);
      if (d.dshDirs.length) {
        console.log(`      DSH 历史目录 ${d.dshDirs.length} 个：\n        ` + d.dshDirs.join("\n        "));
      }
    }
    if (r.missing.length) console.log("  数据库里不存在（跳过）: " + r.missing.join(", "));
    return;
  }
  console.log("\n=== 已删除 ===");
  let msgs = 0, dirs = 0, pins = 0, chans = 0, refs = 0;
  for (const d of r.removed) {
    msgs += d.messages; dirs += d.dshDirsStashed.length;
    pins += d.pins; chans += d.channels + d.channelSessions;
    refs += d.fileRefs;
    console.log(`  · ${d.name}  (${d.id})`);
    console.log(`      消息 ${d.messages} 条`
      + (d.dshDirsStashed.length ? ` · 运行时目录 ${d.dshDirsStashed.length} 个` : "")
      + (d.pins ? ` · 置顶 ${d.pins} 条` : ""));
    for (const e of d.dirErrors) console.log("      ⚠ " + e);
  }
  console.log(`合计：会话 ${r.removed.length} 个 · 消息 ${msgs} 条 · 运行时目录 ${dirs} 个`
    + (refs ? ` · 附件引用 ${refs} 条` : "")
    + (pins ? ` · 置顶 ${pins} 条` : "")
    + (chans ? ` · IM 渠道绑定 ${chans} 条` : ""));
  if (r.missing.length) console.log("数据库里不存在（跳过）: " + r.missing.join(", "));
  if (r.journal) {
    console.log(`删除留底：${r.journal.dir}`);
    console.log(`          （${humanSize(r.journal.bytes)}，只装这次删掉的那几行；`
      + `要还原用 --restore-deleted）`);
  } else {
    console.log("删除留底：没有留（使用了 --no-journal）");
  }
  if (r.backup) console.log("整库备份：" + r.backup);
}

if (argv.includes("--list-deleted")) {
  const all = listDeletedJournals(dataDir);
  if (!all.length) { console.log("(没有删除留底)"); process.exit(0); }
  const total = all.reduce((a, b) => a + b.size, 0);
  console.log(`删除留底 ${all.length} 份，共 ${humanSize(total)}（新的在前）：`);
  for (const j of all) {
    console.log(`  ${new Date(j.mtime).toLocaleString()}  ${humanSize(j.size).padStart(9)}  ${j.dir}`);
    for (const s of j.sessions) {
      console.log(`      · ${s.name}  (${s.id})  ${s.messages} 条消息`);
    }
  }
  process.exit(0);
}

if (argv.includes("--prune-deleted")) {
  const keep = Number(argOf(argv, "--keep", "5"));
  const r = pruneDeleted({ dataDir, keep, dryRun });
  console.log(`删除留底共 ${r.total} 份，保留最新的 ${r.kept.length} 份。`);
  if (!r.deleted.length) { console.log("没有需要删的。"); process.exit(0); }
  console.log((r.dryRun ? "--dry-run：将会删除 " : "已删除 ")
    + `${r.deleted.length} 份留底`
    + (r.dryRun ? `（共 ${humanSize(r.freedBytes)}），什么都没改：` : `，释放 ${humanSize(r.freedBytes)}：`));
  for (const j of r.deleted) console.log(`  ${new Date(j.mtime).toLocaleString()}  ${j.dir}`);
  process.exit(0);
}

const restoreFile = argOf(argv, "--restore-deleted");
if (restoreFile) {
  const r = restoreDeleted({ file: restoreFile, sessionIds: argOf(argv, "--session"), dataDir });
  console.log(`留底文件：${r.journal}`);
  if (!r.restored.length && !r.skipped.length) {
    console.log("这个留底里没有匹配的会话（--session 是不是写错了？）");
    process.exit(1);
  }
  for (const x of r.restored) {
    console.log(`✅ 已还原：${x.name}  (${x.id})  消息 ${x.messages} 条`
      + (x.fileRefs ? ` · 附件引用 ${x.fileRefs}` : "")
      + (x.pins ? ` · 置顶 ${x.pins}` : "")
      + (x.channelSessions ? ` · 渠道路由 ${x.channelSessions}` : ""));
  }
  for (const x of r.skipped) console.log(`⏭  跳过：${x.name}  (${x.id}) —— ${x.why}`);
  if (r.dirsRestored) console.log(`运行时目录已搬回原位：${r.dirsRestored} 个`);
  for (const m of r.dirsMissing) console.log("⚠ 运行时目录：" + m);
  if (r.fkNew) {
    console.log(`⚠ 还原后多了 ${r.fkNew} 处「指向已不存在的记录」的引用：`
      + "留底里某几行的父记录在这期间被删掉了（界面上可能显示异常）。");
  }
  console.log("\n⚠️ 还原后要重启 Cherry Studio 才会看到。");
  process.exit(0);
}

if (undoId || deleteArg || deleteManyArg) {
  // --undo 只接受 id；--delete 既接受 id 也接受会话名（名字有歧义时会报错）
  let ids;
  if (deleteManyArg) {
    ids = deleteManyArg;
  } else if (deleteArg) {
    const db = openDb(true, dataDir);
    const s = resolveSession(db, deleteArg);
    db.close();
    if (!s) throw new Error("找不到会话: " + deleteArg);
    ids = s.id;
  } else {
    ids = undoId;
  }

  const preview = inspectForDelete({ ids, dataDir });
  const alive = preview.filter((p) => p.exists);
  if (!alive.length) throw new Error("这些会话在数据库里都不存在：" + preview.map((p) => p.id).join(", "));
  if (alive.length === 1) {
    console.log("会话   :", alive[0].name, "(" + alive[0].id + ")");
    console.log("消息数 :", alive[0].msgCount);
    console.log("运行时 :", alive[0].dshDirs.length ? alive[0].dshDirs.length + " 个 DSH 历史目录" : "(无)");
  } else {
    console.log(`共 ${alive.length} 个会话，消息合计 ${alive.reduce((a, b) => a + b.msgCount, 0)} 条`);
  }
  if (dryRun) {
    printDeleteResult({
      dryRun: true, removed: alive,
      missing: preview.filter((p) => !p.exists).map((p) => p.id),
    });
    process.exit(0);
  }
  const r = removeSessions({
    ids, dataDir,
    backup: argv.includes("--with-backup"),
    journal: !argv.includes("--no-journal"),
  });
  printDeleteResult(r);
  console.log("\n⚠️ 删掉的行不会留在数据库里（只有上面那份留底能还原）。");
  console.log("   要还原：bun duplicate-session.js --restore-deleted \"<留底目录>\\journal.json\"");
  process.exit(0);
}

if (!target) {
  console.log("用法: bun duplicate-session.js --session <sessionId 或名字> [--name 新名字] [--dry-run]");
  console.log("      bun duplicate-session.js --delete <sessionId 或名字> [--dry-run]");
  console.log("      bun duplicate-session.js --delete-many <id,id,...> [--dry-run]");
  console.log("      bun duplicate-session.js --undo <newSessionId>");
  console.log("      bun duplicate-session.js --list [--agent dsh]");
  console.log("      bun duplicate-session.js --list-copies [--agent dsh]");
  console.log("      bun duplicate-session.js --backup");
  console.log("      bun duplicate-session.js --list-backups");
  console.log("      bun duplicate-session.js --prune-backups [--keep 5] [--dry-run]");
  console.log("      bun duplicate-session.js --list-deleted");
  console.log("      bun duplicate-session.js --restore-deleted <留底里的 journal.json> [--session <id>]");
  console.log("      bun duplicate-session.js --prune-deleted [--keep 5] [--dry-run]");
  console.log("      bun duplicate-session.js --list-data-dir");
  console.log("");
  console.log("  公共参数: --data-dir <目录>   指定 Cherry 数据目录（默认自动查找）");
  console.log("");
  console.log("图形化/点选入口：同目录 pick.js，或桌面上的「会话复制中心」快捷方式。");
  process.exit(1);
}

const db = openDb(true, dataDir);
const src = resolveSession(db, target);
if (!src) { db.close(); console.log("没找到会话:", target); process.exit(1); }
const messages = db.query(
  "SELECT COUNT(*) c FROM agent_session_message WHERE session_id = ? AND status != 'error' AND delivery_sender_session_id IS NULL"
).get(src.id).c;
db.close();

const dshDirs = findDshSessionDirs(src.id, dataDir);
console.log("数据目录:", dbPathOfForLog(dataDir));
console.log("源会话 :", src.name, "(" + src.id + ")");
console.log("消息数 :", messages);
console.log("工作区 :", src.workspace_id);
console.log("DSH 历史目录:", dshDirs.length ? dshDirs.join(", ") : "(无)");

if (dryRun) { console.log("\n--dry-run: 不做任何写入"); process.exit(0); }

console.log("\n复制中……");
const r = duplicateSession({ sessionId: src.id, newName: newNameArg, dataDir,
                             backup: argv.includes("--with-backup") });
console.log("");
console.log("=== 完成 ===");
console.log("新会话 id  :", r.newId);
console.log("新会话名字 :", r.newName);
console.log("order_key  :", r.orderKey);
console.log("消息已复制 :", r.msgCount);
console.log("DSH 历史   :", r.dshDir ?? "(源会话没有 dsh 历史，跳过)");
console.log("整库备份   :", r.backup ?? "(没做；要的话加 --with-backup)");
console.log("\n撤销: bun duplicate-session.js --undo " + r.newId);

function dbPathOfForLog(d) {
  try { return dbPathOf(d); } catch { return "(未找到)"; }
}
