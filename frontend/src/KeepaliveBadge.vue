<script setup>
import { computed } from "vue";
const props = defineProps({ value: { type: Object, default: null } });
const title = computed(() => {
  const v = props.value;
  if (!v) return "";
  const date = v.last_played_at ? new Date(v.last_played_at * 1000).toLocaleString("zh-CN", { hour12: false }) : "未知";
  return `保号要求 ${v.limit} 天 · ${v.days != null ? `未观看 ${v.days} 天` : v.error || "暂无播放记录"} · 最近播放 ${date}${v.error && v.days != null ? ` · 查询失败：${v.error}` : ""}`;
});
</script>
<template>
  <span v-if="value" class="keepalive-badge" :class="value.state" :title="title" :aria-label="title">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="13" r="8" /><path d="M12 9v4l2 2M3 4l3-2M18 2l3 2M6 20l-1 2M18 20l1 2" />
    </svg><span v-if="value.days != null">{{ value.days }}</span><span v-else-if="value.state !== 'error'">—</span>
  </span>
</template>
