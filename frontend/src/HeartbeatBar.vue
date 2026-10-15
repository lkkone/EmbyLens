<script setup>
import { computed } from "vue";
const props = defineProps({
  history: { type: Array, default: () => [] },
  policy: { type: Object, default: () => ({}) },
});
const label = {
  healthy: "正常",
  slow: "响应偏慢或部分线路异常",
  degraded: "响应过慢",
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
    <div class="beats" aria-label="最近 5 小时的真实检测记录">
      <span
        v-for="(h, index) in bars"
        :key="h.at"
        class="beat"
        :class="h.state"
        :style="index === 0 ? { gridColumn: 61 - bars.length } : undefined"
        :title="h.title"
      ></span>
    </div>
    <div class="beat-time"><span>{{ history.length ? new Date(history[0].at * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "暂无检测记录" }}</span><span>最近检测</span></div>
  </div>
</template>
