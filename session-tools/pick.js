// Cherry Studio 会话复制 - 点选菜单（双击「会话复制中心」进的就是这里）
// 也可以非交互调用：--action list|copy|remove|help
import { openDb, listSessions, resolveSession, duplicateSession, removeSession, argOf } from "./session-lib.js";

let lastList = [];   // 当前列表（序号 -> 会话）

const argv = process.argv.slice(2);
const action = argOf(argv, "--action");
const dataDir = argOf(argv, "--data-dir");

process.on("uncaughtException", (e) => {
  console.error("❌ " + String((e && e.message) || e).split("\n").join("\n   "));
  process.exit(1);
});

// 逐行读 stdin（不用 readline，保证按钮式调用与管道输入都能稳定工作）
const input = await Bun.stdin.text();
const lines = input.split(/\r?\n/);
let cursor = 0;
const ask = async (q) => {
  process.stdout.write(q);
  if (cursor >= lines.length) return "";       // 没有更多输入了，返回空串
  const a = lines[cursor++];
  process.stdout.write(a + "\n");              // 回显，方便看日志
  return a.trim();
};

if (action === "list") { await cmdList(); process.exit(0); }
if (action === "copy") {
  finish(duplicateSession({ sessionId: argOf(argv, "--session"), newName: argOf(argv, "--name"), dataDir }));
  process.exit(0);
}
if (action === "remove") {
  const r = removeSession({ sessionId: argOf(argv, "--session"), dataDir });
  console.log(`\n已删除副本「${r.name}」( ${r.id} )`);
  process.exit(0);
}
if (action === "help") { help(); process.exit(0); }

// 交互模式
console.log("========================================");
console.log("  Cherry 会话复制中心");
console.log("========================================\n");
await cmdList();
console.log("");
for (;;) {
  console.log("输入序号  = 复制这条会话");
  console.log("删 序号   = 删除某个副本     (例如：删 3)");
  console.log("名字/ID   = 直接指定会话     列 = 刷新列表   退 = 退出\n");
  const line = await ask("> ");
  if (!line) { console.log("\n（没有收到选择，先退出。重新运行可继续。）"); break; }
  const t = line.replace(/[（(].*$/, "").trim();

  if (/^(退|exit|q|quit)$/i.test(t)) break;
  if (/^(列|list|ls|刷新)$/i.test(t)) { console.log(""); await cmdList(); console.log(""); continue; }
  if (/^(帮|help|\?|？)$/i.test(t)) { help(); continue; }

  const del = t.match(/^(删|del|delete|rm)\s*(.*)$/i);
  if (del) { await removeFlow((del[2] || "").trim()); continue; }

  await copyFlow(t);
}

console.log("\n再见。");

// ---------- 列表 ----------
async function cmdList() {
  const db = openDb(true, dataDir);
  const rows = listSessions(db, {});
  db.close();
  lastList = rows;
  console.log(`共 ${rows.length} 条会话（按创建时间倒序）：\n`);
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const nm = (r.name || "(未命名)").replace(/\s+/g, " ").slice(0, 40);
    console.log(`${String(i + 1).padStart(3)}. ${nm.padEnd(42)} ${String(r.msg_count).padStart(3)}条  ${r.agent_name || "?"}`);
  }
}

function indexToSession(who) {
  const idx = Number(who);
  if (!Number.isInteger(idx) || idx < 1 || idx > lastList.length) return null;
  return lastList[idx - 1];
}

// ---------- 复制 ----------
async function copyFlow(who) {
  const db = openDb(true, dataDir);
  let src = null;
  if (/^\d+$/.test(who)) {
    const row = indexToSession(who);
    if (!row) { console.log("\n序号超出范围。\n"); db.close(); return; }
    src = resolveSession(db, row.id);
  } else {
    try { src = resolveSession(db, who); }
    catch (e) { console.log("\n" + e.message + "\n"); db.close(); return; }
  }
  const msgCount = src ? db.query("SELECT COUNT(*) c FROM agent_session_message WHERE session_id=? AND status != 'error'").get(src.id).c : 0;
  db.close();
  if (!src) { console.log(`\n没找到会话「${who}」。\n`); return; }

  console.log(`\n即将复制：`);
  console.log(`  名字：${src.name}`);
  console.log(`  ID  ：${src.id}`);
  console.log(`  消息：${msgCount} 条`);

  const name = await ask(`\n新会话叫什么名字？（直接回车 = ${src.name} (副本)）> `);
  console.log("\n复制中……");
  try {
    finish(duplicateSession({ sessionId: src.id, newName: name || null, dataDir }));
  } catch (e) {
    console.log("\n复制失败：" + (e.message || e) + "\n");
  }
}

// ---------- 删除 ----------
async function removeFlow(who) {
  if (!who) { console.log("\n没指定要删哪个，例如：删 3\n"); return; }
  const db = openDb(true, dataDir);
  let s = null;
  if (/^\d+$/.test(who)) {
    const row = indexToSession(who);
    if (!row) { console.log("\n序号超出范围。\n"); db.close(); return; }
    s = resolveSession(db, row.id);
  } else {
    try { s = resolveSession(db, who); } catch (e) { console.log("\n" + e.message + "\n"); db.close(); return; }
  }
  db.close();
  if (!s) { console.log(`\n没找到会话「${who}」。\n`); return; }
  const ok = await ask(`\n确定删除「${s.name}」( ${s.id} )？删了找不回来。(y/N) > `);
  if (!/^y(es)?$/i.test(ok)) { console.log("\n已取消。\n"); return; }
  const r = removeSession({ sessionId: s.id, dataDir });
  console.log(`\n已删除「${r.name}」。\n`);
}

function finish(r) {
  console.log("");
  console.log("================ 复制完成 ================");
  console.log("  新会话  :", r.newName);
  console.log("  新 ID   :", r.newId);
  console.log("  消息    :", r.msgCount, "条");
  console.log("  日志    :", r.dshDir ? "已同步" : "(源会话没有 DSH 历史)");
  console.log("  备份    :", r.backup);
  console.log("==========================================");
  console.log("回 Cherry Studio 切一下会话列表就能看到，不用重启。");
  console.log("");
}

function help() {
  console.log(`
用法（在菜单里直接输入）：
  1 / 2 / 3 ……   按列表序号复制那条会话
  名字 或 ID      直接指定要复制的会话
  删 序号         删除某个副本
  列              刷新列表
  退              退出
`);
}
