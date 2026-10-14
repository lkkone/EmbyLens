<script setup>
import { ref, watch, onUnmounted } from "vue";
const revealed = ref("");
let hideTimer;
function hide() {
  revealed.value = "";
  clearTimeout(hideTimer);
}
function reveal(password) {
  hide();
  revealed.value = password;
  hideTimer = setTimeout(hide, 30000);
}
defineExpose({ reveal, hide });
watch(() => props.server?.id, hide);
watch(() => props.form.password, hide);
onUnmounted(hide);
const props = defineProps({
  form: Object,
  server: Object,
  busy: Boolean,
  error: String,
});
defineEmits(["save", "delete", "export", "detail", "reveal"]);
</script>
<template>
  <section class="editor panel">
    <div class="editor-heading">
      <h2>{{ server ? server.name : "接入 Emby" }}</h2>
      <div v-if="server" class="editor-tools">
        <button class="btn" :disabled="busy" @click="$emit('detail')">
          查看状态</button
        ><button class="btn" :disabled="busy" @click="$emit('export')">
          复制配置
        </button>
      </div>
    </div>
    <form @submit.prevent="$emit('save')">
      <h3>基本信息</h3>
      <div class="form-grid">
        <label
          >服务器名称<input
            v-model="form.display_name"
            maxlength="200"
            placeholder="留空则自动获取名称" /></label
        ><label
          >分组<input v-model="form.group" maxlength="200" required
        /></label>
      </div>
      <label
        >备注<input
          v-model="form.notes"
          maxlength="1000"
          placeholder="可选，仅在管理后台显示"
      /></label>
      <h3>连接线路</h3>
      <div class="route-heading"><span>主线路</span><label class="proxy-toggle"><input v-model="form.primary_use_proxy" type="checkbox" role="switch" aria-label="主线路使用代理" />使用代理</label></div>
      <label
        ><input
          v-model="form.primary_url"
          aria-label="主线路地址"
          type="url"
          placeholder="https://emby.example.com"
          required
      /></label>
      <div class="secondary-section">
        <div class="secondary-heading"><span>备用服务器</span></div>
        <div class="secondary-slots">
          <section
            v-for="(line, index) in form.secondary_lines"
            :key="index"
            class="secondary-slot"
          >
            <div class="route-heading"><h4>备用{{ index + 1 }}</h4><label class="proxy-toggle"><input v-model="line.use_proxy" type="checkbox" role="switch" :aria-label="`备用${index + 1}使用代理`" />使用代理</label></div>
            <label
              >名称<input
                v-model="line.name"
                maxlength="100"
                placeholder="线路名称"
                :required="Boolean(line.url.trim())"
            /></label>
            <label
              >地址<input
                v-model="line.url"
                type="url"
                placeholder="https://…"
                :required="Boolean(line.name.trim())"
            /></label>
          </section>
        </div>
      </div>
      <div class="form-grid">
        <label
          >客户端标识<select
            v-model="form.client_name"
            @change="
              form.user_agent =
                form.client_name === 'auto'
                  ? form.user_agent
                  : form.client_name
            "
          >
            <option value="Forward">Forward</option>
            <option value="Infuse">Infuse</option>
            <option value="ReX">ReX</option>
            <option v-if="form.client_name === 'auto'" value="auto">
              沿用已验证格式
            </option>
            <option
              v-if="
                form.client_name &&
                !['Forward', 'Infuse', 'ReX', 'EmbyLens', 'auto'].includes(
                  form.client_name,
                )
              "
              :value="form.client_name"
            >
              {{ form.client_name }}
            </option>
          </select></label
        >
        <label
          >请求标识（User-Agent）<input
            v-model="form.user_agent"
            maxlength="200"
            placeholder="Forward"
        /></label>
      </div>

      <h3>Emby 账号</h3>
      <div class="form-grid">
        <label
          >用户名称<input
            v-model="form.username"
            autocomplete="off"
            maxlength="200"
            required /></label
        ><label
          >用户密码
          <div class="password-field">
            <input
              :value="revealed || form.password"
              @input="
                hide();
                form.password = $event.target.value;
              "
              :type="revealed ? 'text' : 'password'"
              autocomplete="new-password"
              maxlength="1024"
              :required="!server?.has_password"
              :placeholder="
                server?.has_password
                  ? '已保存，留空保留原密码'
                  : 'Emby 账号密码'
              "
            /><button
              type="button"
              class="password-eye"
              :disabled="busy || !server"
              :aria-label="revealed ? '隐藏密码' : '查看密码'"
              @click="revealed ? hide() : $emit('reveal')"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
                <circle cx="12" cy="12" r="3" />
                <path v-if="revealed" d="M3 3l18 18" />
              </svg>
            </button></div
        ></label>
      </div>

      <p class="form-error" role="alert">{{ error }}</p>
      <div class="actions">
        <button
          v-if="server"
          type="button"
          class="btn danger"
          :disabled="busy"
          @click="$emit('delete')"
        >
          删除服务器</button
        ><button class="btn primary" :disabled="busy">
          {{ busy ? "正在保存…" : server ? "保存" : "接入并采集" }}
        </button>
      </div>
    </form>
  </section>
</template>
