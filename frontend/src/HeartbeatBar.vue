<script setup>
import { computed } from "vue";
const props = defineProps({
  history: { type: Array, default: () => [] },
  policy: { type: Object, default: () => ({}) },
});
const label = {
  healthy: "正常",
  slow: "部分线路异常",
  degraded: "线路异常",
  error: "全部线路不可用",
  unknown: "在线，尚无有效延迟",
  direct: "在线，直连模式",
  offline: "未采集 / 采集已中断",
};
const bars = computed(() =>
  props.history.map((h) => ({
    ...h,
    title: `${new Date(h.at * 1000).toLocaleString("zh-CN", { hour12: false })} · ${label[h.state] || h.state}${h.delay_ms != null ? ` · ${h.delay_ms} ms` : ""}`,
  })),
);
</script>
<template>
  <div class="history">
    <div class="beats" aria-label="最近 5 小时，Emby 可用性历史">
      <span
        v-for="h in bars"
        :key="h.at"
        class="beat"
        :class="h.state"
        :title="h.title"
      ></span>
    </div>
    <div class="beat-time"><span>5 小时前</span><span>现在</span></div>
  </div>
</template>
