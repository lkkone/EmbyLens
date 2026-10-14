<script setup>
import { computed, onMounted, onUnmounted, ref, nextTick, watch } from "vue";
import { createFaviconBadge } from "./faviconBadge.js";
import HeartbeatBar from "./HeartbeatBar.vue";
import ServerEditor from "./ServerEditor.vue";
const admin = location.pathname.replace(/\/$/, "") === "/admin";
document.title = admin ? "咸鱼的 Emby 管理" : "咸鱼的 Emby 状态";
document.documentElement.dataset.theme = matchMedia(
  "(prefers-color-scheme:dark)",
).matches
  ? "dark"
  : "light";
const servers = ref([]),
  authenticated = ref(false),
  loading = ref(true),
  failure = ref(""),
  username = ref(""),
  password = ref(""),
  authError = ref(""),
  busy = ref(false),
  formError = ref(""),
  editingId = ref(null),
  notice = ref(""),
  dialog = ref(null),
  activeId = ref(null);
const lineSlots = (lines = []) =>
  Array.from({ length: 2 }, (_, slot) => ({
    name: "",
    url: "",
    use_proxy: false,
    ...lines.find((line, index) => (line.slot ?? index) === slot),
    slot,
  }));
const editor = ref(null),
  securityDialog = ref(null),
  securityCode = ref(""),
  secretAction = ref(""),
  secretError = ref(""),
  secretBusy = ref(false);
let secretRequest = 0;
function clearSecret() {
  secretRequest += 1;
  secretBusy.value = false;
  securityCode.value = "";
  secretError.value = "";
  secretAction.value = "";
}
async function requestSecret(action) {
  clearSecret();
  secretAction.value = action;
  await nextTick();
  securityDialog.value.showModal();
}
async function unlock() {
  const request = ++secretRequest;
  const serverId = editingId.value;
  const action = secretAction.value;
  secretBusy.value = true;
  secretError.value = "";
  try {
    const r = await api(`/api/servers/${serverId}/secret`, "POST", {
      security_code: securityCode.value,
      action,
    });
    if (request !== secretRequest || serverId !== editingId.value) return;
    securityCode.value = "";
    if (action === "password") {
      editor.value.reveal(r.password);
      securityDialog.value.close();
      clearSecret();
    } else {
      try {
        await navigator.clipboard.writeText(r.content);
      } catch {
        throw new Error("无法访问剪切板，请允许剪切板访问后重试");
      }
      securityDialog.value.close();
      clearSecret();
      notice.value = "配置已复制";
      setTimeout(() => (notice.value = ""), 3000);
    }
  } catch (e) {
    if (request === secretRequest) {
      securityCode.value = "";
      secretError.value = e.message;
    }
  } finally {
    if (request === secretRequest) secretBusy.value = false;
  }
}
const blank = () => ({
  group: "我的 Emby",
  primary_url: "",
  primary_use_proxy: false,
  display_name: "",
  secondary_lines: lineSlots(),
  user_agent: "Forward",
  client_name: "Forward",
  username: "",
  password: "",
  notes: "",
});
const form = ref(blank());
const labels = {
  healthy: "正常",
  slow: "部分线路异常",
  error: "异常",
  pending: "等待采集",
  offline: "采集已中断",
};
const groups = computed(() =>
  [...new Set(servers.value.map((s) => s.group))].map((name) => ({
    name,
    servers: servers.value.filter((s) => s.group === name),
  })),
);
const editing = computed(() =>
  servers.value.find((s) => s.id === editingId.value),
);
const active = computed(() =>
  servers.value.find((s) => s.id === activeId.value),
);
const faviconBadge = createFaviconBadge();
watch(
  () => servers.value.filter((s) => ["slow", "error", "offline"].includes(s.state)).length,
  (count) => faviconBadge.update(count),
  { immediate: true },
);
onUnmounted(() => faviconBadge.dispose());
const overall = computed(() => {
  if (!servers.value.length)
    return { state: "pending", text: "等待接入 Emby 服务器", symbol: "○" };
  let n = servers.value.filter((s) => s.state !== "healthy").length;
  return n
    ? {
        state: servers.value.every((s) =>
          ["error", "offline"].includes(s.state),
        )
          ? "error"
          : "slow",
        text: `${n} 个 Emby 服务器需要关注`,
        symbol: "!",
      }
    : { state: "healthy", text: "所有 Emby 服务器运行正常", symbol: "✓" };
});
const count = (v) => (v == null ? "—" : Number(v).toLocaleString("zh-CN"));
const time = (v) =>
  v
    ? new Date(v * 1000).toLocaleString("zh-CN", {
        hour12: false,
        timeZone: "Asia/Shanghai",
      })
    : "尚未采集";
async function api(path, method = "GET", data) {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-EmbyLens-Request": "1" },
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(20000),
  });
  const b = await r.json();
  if (!r.ok) {
    if (r.status === 401 && path != "/api/login") authenticated.value = false;
    throw Error(b.error || "请求失败");
  }
  return b;
}
let refreshSequence = 0;
let appliedSequence = 0;
const updates = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("embylens-config") : null;
if (updates) updates.onmessage = () => refresh();
function refreshVisible() { if (!document.hidden) refresh(); }
async function refresh() {
  const sequence = ++refreshSequence;
  try {
    const result = (
      await api(admin && authenticated.value ? "/api/servers" : "/api/status")
    ).servers;
    if (sequence < appliedSequence) return;
    appliedSequence = sequence;
    servers.value = result;
    failure.value = "";
  } catch (e) {
    if (sequence < appliedSequence) return;
    appliedSequence = sequence;
    failure.value = e.message;
  } finally {
    loading.value = false;
  }
}
function edit(s) {
  editor.value?.hide();
  editingId.value = s?.id || null;
  form.value = s
    ? {
        ...blank(),
        ...s.config,
        secondary_lines: lineSlots(s.config.secondary_lines),
        group: s.group,
        notes: s.notes,
        password: "",
      }
    : blank();
  formError.value = "";
}
async function login() {
  busy.value = true;
  authError.value = "";
  try {
    await api("/api/login", "POST", {
      username: username.value,
      password: password.value,
    });
    password.value = "";
    authenticated.value = true;
    await refresh();
    edit(servers.value[0]);
  } catch (e) {
    authError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function logout() {
  editor.value?.hide();
  clearSecret();
  await api("/api/logout", "POST", {});
  authenticated.value = false;
  form.value = blank();
  await refresh();
}
async function save() {
  editor.value?.hide();
  busy.value = true;
  formError.value = "";
  try {
    const r = await api(
      editingId.value ? `/api/servers/${editingId.value}` : "/api/servers",
      editingId.value ? "PATCH" : "POST",
      {
        ...form.value,
        secondary_lines: form.value.secondary_lines.filter(
          (line) => line.name.trim() || line.url.trim(),
        ),
      },
    );
    updates?.postMessage("saved");
    editingId.value = r.id || editingId.value;
    form.value.password = "";
    await refresh();
    notice.value = "已保存";
    setTimeout(() => (notice.value = ""), 4000);
  } catch (e) {
    formError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function remove() {
  if (!confirm(`删除 ${editing.value?.name} 及其监控历史？`)) return;
  busy.value = true;
  try {
    await api(`/api/servers/${editingId.value}`, "DELETE", {});
    updates?.postMessage("deleted");
    await refresh();
    edit(servers.value[0]);
  } catch (e) {
    formError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function rescan() {
  busy.value = true;
  try {
    await api(`/api/servers/${editingId.value}/refresh`, "POST", {});
    notice.value = "已安排重新采集";
    setTimeout(() => (notice.value = ""), 4000);
  } catch (e) {
    formError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function detail(s) {
  activeId.value = s.id;
  await nextTick();
  dialog.value.showModal();
}
let timer;
onMounted(async () => {
  if (admin) {
    try {
      authenticated.value = (await api("/api/auth")).authenticated;
    } catch (e) {
      authError.value = e.message;
    }
  }
  await refresh();
  if (admin && authenticated.value) edit(servers.value[0]);
  timer = setInterval(refresh, 5000);
  document.addEventListener("visibilitychange", refreshVisible);
  window.addEventListener("focus", refreshVisible);
});
onUnmounted(() => {
  clearInterval(timer);
  updates?.close();
  document.removeEventListener("visibilitychange", refreshVisible);
  window.removeEventListener("focus", refreshVisible);
});
</script>
<template>
  <main v-if="admin && !authenticated" class="login panel">
    <img src="/avatar.png" alt="" class="avatar" />
    <h1>咸鱼的 Emby 管理</h1>
    <form @submit.prevent="login">
      <label
        >账号<input
          v-model="username"
          autocomplete="username"
          required /></label
      ><label
        >密码<input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required /></label
      ><button class="btn primary" :disabled="busy">
        {{ busy ? "正在登录…" : "登录" }}
      </button>
      <p class="form-error" role="alert">{{ authError }}</p>
    </form>
    <a href="/">返回展示页</a>
  </main>
  <main v-else class="page" :class="{ 'admin-page': admin }">
    <div v-if="admin" class="admin-toolbar">
      <a :href="admin ? '/' : '/admin'">{{ admin ? "← 展示页" : "管理" }}</a>
      <div>
        <button v-if="admin" class="btn" @click="logout">退出</button>
      </div>
    </div>
    <header class="page-title">
      <img src="/avatar.png" class="avatar" alt="" />
      <h1>{{ admin ? "咸鱼的 Emby 管理" : "咸鱼的 Emby 状态" }}</h1>
    </header>
    <p v-if="failure" class="connection-error" role="alert">
      连接中断，正在展示上次收到的数据。
    </p>
    <div v-if="admin" class="admin-layout">
      <aside class="device-sidebar panel">
        <div class="sidebar-inner">
          <button class="btn add-device" @click="edit()">＋ 接入 Emby</button>
          <div class="sidebar-scroll">
            <section v-for="g in groups" :key="g.name" class="sidebar-group">
              <h3>{{ g.name }}</h3>
              <button
                v-for="s in g.servers"
                :key="s.id"
                class="sidebar-device"
                :class="[s.state, { selected: editingId === s.id }]"
                @click="edit(s)"
              >
                <span class="status-dot"></span
                ><span class="sidebar-name"
                  >{{ s.name }}<small>{{ labels[s.state] }}</small></span
                >
              </button>
            </section>
            <p v-if="!servers.length" class="hint sidebar-empty">暂无服务器</p>
          </div>
        </div>
      </aside>
      <ServerEditor
        ref="editor"
        :form="form"
        :server="editing"
        :busy="busy"
        :error="formError"
        @save="save"
        @delete="remove"
        @export="requestSecret('export')"
        @reveal="requestSecret('password')"
        @detail="detail(editing)"
      />
    </div>
    <template v-else>
      <section class="overall panel" :class="overall.state" aria-live="polite">
        <span class="overall-icon">{{ overall.symbol }}</span>
        <h2>{{ loading ? "正在获取 Emby 状态…" : overall.text }}</h2>
      </section>
      <section v-if="!loading && !servers.length" class="empty panel">
        <p>尚未接入 Emby 服务器。</p>
        <a href="/admin">前往管理后台接入</a>
      </section>
      <section v-for="g in groups" :key="g.name" class="group">
        <h2 class="group-title">{{ g.name }}</h2>
        <div class="device-list panel">
          <article
            v-for="s in g.servers"
            :key="s.id"
            class="device emby-device"
            :class="s.state"
          >
            <div class="emby-summary">
              <div class="device-name-row">
                <span class="status-dot"></span
                ><button class="device-name" @click="detail(s)">
                  {{ s.name }}</button
                ><span
                  v-for="(l, index) in s.lines || []"
                  :key="index"
                  class="line-status"
                  :class="l.state"
                  :title="l.error || l.delay_ms + ' ms'"
                  ><i class="status-dot"></i>{{ l.label
                  }}<small>{{
                    l.state === "healthy"
                      ? `${l.delay_ms} ms`
                      : l.error || "等待采集"
                  }}</small></span
                >
              </div>
              <div class="emby-stats">
                <div>
                  <small>电影</small
                  ><strong>{{ count(s.stats?.movies) }}<em> 部</em></strong>
                </div>
                <div>
                  <small>剧集</small
                  ><strong>{{ count(s.stats?.series) }}<em> 部</em></strong>
                </div>
                <div>
                  <small>单集</small
                  ><strong>{{ count(s.stats?.episodes) }}<em> 集</em></strong>
                </div>
                <div>
                  <small>今日新增</small
                  ><strong class="today"
                    >{{
                      s.stats?.day ===
                      new Date().toLocaleDateString("en-CA", {
                        timeZone: "Asia/Shanghai",
                      })
                        ? count(s.stats?.today)
                        : "—"
                    }}<em> 条</em></strong
                  >
                </div>
              </div>
              <p class="last-seen">
                {{
                  s.stats_error
                    ? "数量采集失败 · " + s.stats_error
                    : "更新于 " + time(s.stats_at)
                }}<span v-if="s.error"> · {{ s.error }}</span>
              </p>
            </div>
            <HeartbeatBar :history="s.history" />
          </article>
        </div>
      </section>
    </template>
  </main>
  <dialog
    ref="securityDialog"
    class="security-dialog"
    @close="clearSecret"
    @cancel="clearSecret"
  >
    <div class="dialog-head">
      <h2>{{ secretAction === "password" ? "查看密码" : "复制配置" }}</h2>
      <button
        class="btn icon"
        aria-label="关闭安全码窗口"
        :disabled="secretBusy"
        @click="securityDialog.close()"
      >
        ✕
      </button>
    </div>
    <form @submit.prevent="unlock">
      <label
        >安全码<input
          v-model="securityCode"
          type="password"
          autocomplete="off"
          maxlength="200"
          autofocus
          required
      /></label>
      <p v-if="secretError" class="form-error" role="alert">
        {{ secretError }}
      </p>
      <div class="actions">
        <button class="btn primary" :disabled="secretBusy">
          {{ secretBusy ? "验证中…" : "确认" }}
        </button>
      </div>
    </form>
  </dialog>
  <dialog ref="dialog">
    <div class="dialog-head">
      <h2>{{ active?.name }}</h2>
      <button class="btn icon" aria-label="关闭" @click="dialog.close()">
        ✕
      </button>
    </div>
    <template v-if="active"
      ><div class="detail-lines">
        <div
          v-for="(l, index) in active.lines || []"
          :key="index"
          :class="l.state"
        >
          <span class="status-dot"></span> {{ l.label }} ·
          {{ l.state === "healthy" ? `正常 · ${l.delay_ms} ms` : l.error }}
        </div>
      </div>
      <dl class="facts">
        <div>
          <dt>媒体总数（电影 + 单集）</dt>
          <dd>{{ count(active.stats?.total) }}</dd>
        </div>
        <div>
          <dt>服务器版本</dt>
          <dd>{{ active.version || "—" }}</dd>
        </div>
        <div>
          <dt>最近线路检测</dt>
          <dd>{{ time(active.checked_at) }}</dd>
        </div>
        <div>
          <dt>最近数量采集</dt>
          <dd>{{ time(active.stats_at) }}</dd>
        </div>
      </dl>
      <h3>最近添加</h3>
      <ul class="recent-list">
        <li v-for="(i, n) in active.stats?.recent || []" :key="n">
          <span>{{ i.series ? i.series + " · " : "" }}{{ i.name }}</span
          ><small
            >{{ i.type === "Movie" ? "电影" : "单集" }} ·
            {{
              i.added
                ? new Date(i.added).toLocaleDateString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                  })
                : "日期未知"
            }}</small
          >
        </li>
      </ul>
      <p v-if="!active.stats?.recent?.length" class="hint">
        暂无采集结果
      </p></template
    >
  </dialog>
  <div v-if="notice" class="notice" role="status">{{ notice }}</div>
</template>
