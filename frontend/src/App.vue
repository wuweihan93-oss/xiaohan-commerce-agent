<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import {
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  Clock3,
  Coffee,
  Headphones,
  LampDesk,
  Layers3,
  LoaderCircle,
  Menu,
  MessageCircle,
  Package,
  Plus,
  ShoppingBag,
  Sparkles,
  Square,
  Truck,
  X,
} from "lucide-vue-next";
import { readEventStream } from "./stream.js";
import { renderMarkdown } from "./markdown.js";
import { createId } from "./id.js";

const session = ref(null),
  health = ref(null),
  products = ref([]),
  orders = ref([]),
  actions = ref([]),
  history = ref([]);
const conversationId = ref(createId()),
  messages = ref([]),
  question = ref(""),
  error = ref(""),
  notice = ref("");
const initializing = ref(true),
  loading = ref(false),
  switching = ref(false),
  confirming = ref("");
const mobileNav = ref(false),
  contextOpen = ref(false),
  activeTab = ref("orders"),
  chatScroll = ref(null);
let controller;
let follow = true;
const busy = computed(
  () => initializing.value || loading.value || switching.value,
);
const latest = computed(() =>
  [...messages.value].reverse().find((item) => item.role === "assistant"),
);
const pending = computed(() =>
  actions.value.filter((item) => item.status === "pending"),
);
const quickQuestions = computed(() =>
  session.value?.customer_id === "demo-b"
    ? [
        "查一下我的订单",
        "SG20260003 什么时候发货？",
        "阅读灯需要充电吗？",
        "为 SG20260003 申请取消订单，原因是不需要了",
        "推荐一款适合办公桌阅读的灯，预算200元以内",
        "推荐一款通勤用的降噪耳机，预算500元以内",
      ]
    : [
        "降噪耳机多少钱？还有库存吗？",
        "查一下 SG20260002 的物流",
        "你们支持七天无理由退货吗？",
        "为 SG20260001 申请退货，原因是不合适",
        "推荐一款适合办公桌阅读的灯，预算200元以内",
        "推荐一款通勤用的降噪耳机，预算500元以内",
      ],
);
const icons = [Headphones, Coffee, LampDesk];
const quickLabels = [
  "商品价格与库存",
  "查询订单物流",
  "退换货规则",
  "申请售后",
  "按预算推荐桌灯",
  "按用途推荐耳机",
];
const money = (value) => `¥${(value / 100).toFixed(2)}`;
const actionStatus = (value) =>
  ({
    pending: "等待你的确认",
    submitted: "待人工审核",
    cancelled: "已取消草稿",
  })[value] || value;

async function api(path, options = {}) {
  const response = await fetch("/api" + path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Client-Request": "1",
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(data.detail || `服务请求失败（${response.status}）`);
  return data;
}
async function refresh() {
  const [orderData, actionData, historyData] = await Promise.all([
    api("/orders"),
    api("/actions"),
    api("/conversations"),
  ]);
  orders.value = orderData.orders;
  actions.value = actionData.actions;
  history.value = historyData.conversations;
}
function cacheKey() {
  return `commerce-current:${session.value?.cache_scope}:${session.value?.customer_id}`;
}
function saveCurrent() {
  localStorage.setItem(cacheKey(), conversationId.value);
}
function newConversation() {
  if (busy.value) return;
  conversationId.value = createId();
  messages.value = [];
  question.value = "";
  error.value = "";
  notice.value = "";
  mobileNav.value = false;
  saveCurrent();
}
async function scrollEnd() {
  await nextTick();
  if (follow && chatScroll.value)
    chatScroll.value.scrollTop = chatScroll.value.scrollHeight;
}
function trackScroll() {
  const el = chatScroll.value;
  if (el) follow = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
}
async function openConversation(id) {
  if (busy.value) return;
  switching.value = true;
  error.value = "";
  try {
    const data = await api("/conversations/" + encodeURIComponent(id));
    conversationId.value = id;
    messages.value = data.conversation.messages;
    saveCurrent();
    mobileNav.value = false;
    follow = true;
    await scrollEnd();
  } catch (caught) {
    error.value = caught.message;
  } finally {
    switching.value = false;
  }
}
async function switchCustomer(event) {
  if (busy.value) return;
  switching.value = true;
  error.value = "";
  try {
    await api("/session/customer", {
      method: "POST",
      body: JSON.stringify({ customer_id: event.target.value }),
    });
    session.value = await api("/session");
    messages.value = [];
    conversationId.value = createId();
    saveCurrent();
    await refresh();
    notice.value = "已切换为" + session.value.customer_name;
  } catch (caught) {
    error.value = caught.message;
  } finally {
    switching.value = false;
  }
}
async function ask(text = question.value) {
  if (busy.value || !text.trim()) return;
  const value = text.trim();
  question.value = "";
  error.value = "";
  notice.value = "";
  loading.value = true;
  follow = true;
  messages.value.push({ id: createId(), role: "user", content: value });
  const reply = {
    id: createId(),
    role: "assistant",
    content: "",
    sources: [],
    trace: [],
    recommendations: [],
    streaming: true,
  };
  messages.value.push(reply);
  const target = () => messages.value.find((item) => item.id === reply.id);
  controller = new AbortController();
  let terminal = false;
  await scrollEnd();
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Client-Request": "1" },
      signal: controller.signal,
      body: JSON.stringify({
        question: value,
        conversation_id: conversationId.value,
      }),
    });
    await readEventStream(response, (event, data) => {
      const item = target();
      if (!item) return;
      if (event === "token") item.content += data.text;
      if (event === "reset") item.content = "";
      if (event === "state") {
        item.sources = data.sources;
        item.trace = data.trace;
        item.recommendations = data.recommendations || [];
      }
      if (event === "done") {
        item.content = data.answer;
        item.sources = data.sources;
        item.trace = data.trace;
        item.recommendations = data.recommendations || [];
        terminal = true;
      }
      if (event === "error") {
        item.content = data.answer;
        item.sources = data.sources;
        item.trace = data.trace;
        item.recommendations = data.recommendations || [];
        error.value = data.detail;
        terminal = true;
      }
      scrollEnd();
    });
    if (!terminal)
      throw new Error("连接意外结束，请重试或打开历史记录查看保存结果。");
  } catch (caught) {
    if (caught.name === "AbortError") {
      if (target()) target().content += "\n\n[已停止生成]";
    } else {
      error.value = caught.message;
      if (target() && !target().content)
        target().content = "这次回答未完成，请稍后重试。";
    }
  } finally {
    if (target()) target().streaming = false;
    loading.value = false;
    controller = null;
    saveCurrent();
    try {
      await refresh();
    } catch (caught) {
      error.value = caught.message;
    }
    if (pending.value.length) {
      activeTab.value = "actions";
      contextOpen.value = true;
    }
    await scrollEnd();
  }
}
async function confirmAction(action, confirm) {
  if (confirming.value || busy.value) return;
  confirming.value = action.id;
  error.value = "";
  try {
    const data = await api(`/actions/${action.id}/confirm`, {
      method: "POST",
      body: JSON.stringify({ confirm }),
    });
    notice.value = data.notice;
    await refresh();
  } catch (caught) {
    error.value = caught.message;
  } finally {
    confirming.value = "";
  }
}
async function initialize() {
  try {
    const first = await api("/session");
    session.value = await api("/session");
    if (first.cache_scope !== session.value.cache_scope)
      throw new Error("请允许本站 Cookie 后刷新页面。");
    const [status, catalog] = await Promise.all([
      api("/health"),
      api("/products"),
    ]);
    health.value = status;
    products.value = catalog.products;
    await refresh();
    const saved = localStorage.getItem(cacheKey());
    if (saved && history.value.some((item) => item.id === saved)) {
      const result = await api("/conversations/" + encodeURIComponent(saved));
      conversationId.value = saved;
      messages.value = result.conversation.messages;
    }
    follow = true;
    await scrollEnd();
  } catch (caught) {
    error.value = "初始化失败：" + caught.message;
  } finally {
    initializing.value = false;
  }
}
onMounted(initialize);
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <div class="app-shell">
    <div v-if="mobileNav" class="scrim" @click="mobileNav = false"></div>
    <aside class="sidebar" :class="{ open: mobileNav }">
      <a class="brand" href="/"
        ><span class="brand-icon"><ShoppingBag :size="23" /></span
        ><span><strong>拾光商店</strong><small>演示商店</small></span></a
      >
      <button class="new-chat" :disabled="busy" @click="newConversation">
        <Plus :size="17" />新对话
      </button>
      <div class="sidebar-heading">最近对话</div>
      <div class="conversation-list">
        <button
          v-for="item in history"
          :key="item.id"
          :class="{ selected: item.id === conversationId }"
          :disabled="busy"
          @click="openConversation(item.id)"
        >
          <MessageCircle :size="15" /><span>{{ item.title }}</span>
        </button>
        <p v-if="!history.length">暂无对话</p>
      </div>
      <div class="sidebar-bottom">
        <div class="customer-switch">
          <span class="user-avatar">{{
            session?.customer_name?.slice(0, 1) || "林"
          }}</span>
          <div>
            <small>演示买家</small
            ><select
              :value="session?.customer_id || 'demo-a'"
              :disabled="busy"
              aria-label="切换演示身份"
              @change="switchCustomer"
            >
              <option value="demo-a">林同学 · 买家 A</option>
              <option value="demo-b">陈同学 · 买家 B</option>
            </select>
          </div>
          <ChevronDown :size="14" />
        </div>
        <p class="demo-notice">演示数据 · 不产生真实交易</p>
      </div>
    </aside>
    <main class="main-content">
      <header class="topbar">
        <div class="topbar-left">
          <button
            class="icon-button mobile-only"
            aria-label="打开导航"
            @click="mobileNav = true"
          >
            <Menu :size="21" />
          </button>
          <div class="agent-avatar"><Headphones :size="23" /></div>
          <div>
            <h1>小拾客服</h1>
            <span class="online-status"
              ><i></i>{{ health?.model_configured ? "在线" : "未配置模型" }}</span
            >
          </div>
        </div>
        <button class="context-toggle" @click="contextOpen = !contextOpen">
          <ShoppingBag :size="17" />订单与售后</button
        ><span class="desktop-caption">拾光商店 · 客户服务</span>
      </header>
      <div class="service-layout">
        <section class="chat-section">
          <div ref="chatScroll" class="chat-scroll" @scroll="trackScroll">
            <div v-if="initializing" class="initializing">
              <LoaderCircle :size="20" class="spin" />正在连接…
            </div>
            <div v-else-if="!messages.length" class="welcome-state">
              <div class="welcome-avatar">
                <Headphones :size="33" :stroke-width="1.7" />
              </div>
              <h2>
                你好，{{ session?.customer_name || "朋友"
                }}<span>有什么可以帮你？</span>
              </h2>
              <div class="quick-grid">
                <button
                  v-for="(text, index) in quickQuestions"
                  :key="text"
                  :disabled="busy"
                  @click="ask(text)"
                >
                  <component
                    :is="[ShoppingBag, Truck, BookOpen, Package, Sparkles, Sparkles][index]"
                    :size="20"
                  /><span>{{ quickLabels[index] }}</span
                  ><ArrowRight :size="15" />
                </button>
              </div>
            </div>
            <div
              v-for="message in messages"
              :key="message.id"
              class="message-row"
              :class="message.role"
            >
              <div class="message-avatar">
                <Headphones
                  v-if="message.role === 'assistant'"
                  :size="18"
                /><span v-else>{{ session?.customer_name?.slice(0, 1) }}</span>
              </div>
              <div class="message-content">
                <div v-if="message.role === 'assistant'" class="message-name">
                  小拾客服
                </div>
                <div class="chat-bubble">
                  <div
                    v-if="message.role === 'assistant'"
                    class="markdown"
                    v-html="renderMarkdown(message.content)"
                  ></div>
                  <p v-else class="user-text">{{ message.content }}</p>
                  <span
                    v-if="message.streaming && !message.content"
                    class="thinking"
                    ><LoaderCircle :size="14" class="spin" />正在查询…</span
                  >
                </div>
                <div
                  v-if="message.recommendations?.length"
                  class="recommendation-list"
                  aria-label="商品推荐"
                >
                  <article
                    v-for="product in message.recommendations"
                    :key="product.id"
                    class="recommendation-item"
                  >
                    <div class="recommendation-heading">
                      <div>
                        <small>{{ product.category }}</small>
                        <h3>{{ product.name }}</h3>
                      </div>
                      <strong>{{ money(product.price) }}</strong>
                    </div>
                    <p>{{ product.description }}</p>
                    <div class="recommendation-meta">
                      <span>库存 {{ product.stock }} 件</span>
                      <span
                        v-for="([name, value], index) in Object.entries(product.specs).slice(0, 2)"
                        :key="name"
                      >
                        {{ name }} {{ Array.isArray(value) ? value.join(" / ") : value }}
                      </span>
                    </div>
                    <button
                      class="recommendation-action"
                      :disabled="busy"
                      @click="ask(`再介绍一下${product.name}，重点说适用场景和规格。`)"
                    >
                      继续了解 <ArrowRight :size="14" />
                    </button>
                  </article>
                </div>
                <details v-if="message.sources?.length" class="citations">
                  <summary><BookOpen :size="12" />查看依据</summary>
                  <div v-for="(source, index) in message.sources" :key="index">
                    <strong>{{ source.source }}</strong>
                    <p>{{ source.text }}</p>
                  </div>
                </details>
              </div>
            </div>
          </div>
          <div class="composer-area">
            <div v-if="error" class="feedback error" role="alert">
              {{ error
              }}<button aria-label="关闭错误" @click="error = ''">
                <X :size="15" />
              </button>
            </div>
            <div v-if="notice" class="feedback notice" role="status">
              {{ notice
              }}<button aria-label="关闭提示" @click="notice = ''">
                <X :size="15" />
              </button>
            </div>
            <form class="composer" @submit.prevent="ask()">
              <textarea
                v-model="question"
                :disabled="busy"
                rows="2"
                maxlength="3000"
                placeholder="发消息，问问小拾…"
                aria-label="输入客服问题"
                @keydown.enter.exact.prevent="ask()"
              ></textarea>
              <div class="composer-bottom">
                <span>Shift + Enter 换行</span><button
                  v-if="loading"
                  class="send-button"
                  type="button"
                  aria-label="停止生成"
                  @click="controller?.abort()"
                >
                  <Square :size="16" /></button
                ><button
                  v-else
                  class="send-button"
                  :disabled="busy || !question.trim()"
                  aria-label="发送问题"
                >
                  <ArrowUp :size="21" />
                </button>
              </div>
            </form>
          </div>
        </section>
        <aside class="context-panel" :class="{ open: contextOpen }">
          <div class="panel-header">
            <strong>订单与售后</strong
            ><button
              class="icon-button panel-close"
              aria-label="关闭服务面板"
              @click="contextOpen = false"
            >
              <X :size="19" />
            </button>
          </div>
          <nav class="panel-tabs">
            <button
              :class="{ active: activeTab === 'orders' }"
              @click="activeTab = 'orders'"
            >
              我的订单</button
            ><button
              :class="{ active: activeTab === 'products' }"
              @click="activeTab = 'products'"
            >
              店铺商品</button
            ><button
              :class="{ active: activeTab === 'actions' }"
              @click="activeTab = 'actions'"
            >
              售后申请<i v-if="pending.length">{{ pending.length }}</i>
            </button>
          </nav>
          <div class="panel-scroll">
            <template v-if="activeTab === 'orders'"
              ><article
                v-for="order in orders"
                :key="order.id"
                class="order-card"
              >
                <div class="order-top">
                  <span>{{ order.id }}</span><strong
                    :class="{ delivered: order.status === '已签收' }"
                    >{{ order.status }}</strong
                  >
                </div>
                <div class="order-product">
                  <div class="product-thumb">
                    <component
                      :is="
                        icons[
                          products.findIndex(
                            (item) => item.id === order.product_id,
                          )
                        ] || ShoppingBag
                      "
                      :size="29"
                    />
                  </div>
                  <div>
                    <h3>{{ order.product_name }}</h3>
                    <p>数量 1</p>
                    <strong>{{ money(order.amount) }}</strong>
                  </div>
                </div>
                <div class="logistics-line">
                  <Truck :size="15" /><span>{{ order.logistics.at(-1) }}</span>
                </div>
                <button
                  class="order-query"
                  :disabled="busy"
                  @click="ask(`查一下 ${order.id} 的状态和物流`)"
                >
                  咨询这笔订单<ArrowRight :size="14" />
                </button></article
            ></template>
            <template v-else-if="activeTab === 'products'"
              ><article
                v-for="(product, index) in products"
                :key="product.id"
                class="product-card"
              >
                <div class="catalog-art" :class="`art-${index}`">
                  <component
                    :is="icons[index]"
                    :size="58"
                    :stroke-width="1.2"
                  />
                </div>
                <div class="catalog-info">
                  <h3>{{ product.name }}</h3>
                  <p>{{ product.description }}</p>
                  <div>
                    <strong>{{ money(product.price) }}</strong><small
                      >库存 {{ product.stock }} 件</small
                    >
                  </div>
                  <button
                    :disabled="busy"
                    @click="ask(`介绍一下${product.name}的规格、价格和保修`)"
                  >
                    咨询商品<ArrowRight :size="14" />
                  </button>
                </div></article
            ></template>
            <template v-else
              ><article
                v-for="action in actions"
                :key="action.id"
                class="action-card"
              >
                <div class="action-status">
                  <Clock3 v-if="action.status === 'pending'" :size="16" /><Check
                    v-else
                    :size="16"
                  />{{ actionStatus(action.status) }}
                </div>
                <h3>{{ action.order_id }}</h3>
                <p>{{ action.reason }}</p>
                <small v-if="action.ticket_id"
                  >工单 {{ action.ticket_id }}</small
                >
                <div v-if="action.status === 'pending'" class="confirm-buttons">
                  <button
                    :disabled="busy || !!confirming"
                    @click="confirmAction(action, false)"
                  >
                    取消草稿</button
                  ><button
                    class="confirm-primary"
                    :disabled="busy || !!confirming"
                    @click="confirmAction(action, true)"
                  >
                    {{ confirming === action.id ? "提交中…" : "确认提交" }}
                  </button>
                </div>
                <p class="action-notice">
                  {{
                    action.status === "submitted"
                      ? "待人工审核，尚未退款。"
                      : action.status === "cancelled"
                        ? "草稿已取消。"
                        : "确认后登记工单，不会自动退款。"
                  }}
                </p>
              </article>
              <div v-if="!actions.length" class="empty-actions">
                <Package :size="32" />
                <h3>暂无售后申请</h3>
              </div></template
            >
            <details class="service-details">
              <summary>
                <Layers3 :size="14" />服务详情<ChevronDown :size="13" />
              </summary>
              <div class="details-content">
                <p>
                  模型：{{ health?.model || "未连接" }}。问题和检索结果会发送给配置的模型服务。
                </p>
                <p>
                  店铺知识库：{{ health?.knowledge?.files?.length || 0 }} 份文档
                  · {{ health?.knowledge?.chunks || 0 }} 个片段
                </p>
                <ol v-if="latest?.trace?.length" class="tool-trace">
                  <li
                    v-for="(step, index) in latest.trace"
                    :key="index"
                    :class="step.status"
                  >
                    <span class="step-dot"></span>
                    <div>
                      <strong>{{ step.tool }}</strong>
                      <p>{{ step.label }}</p>
                    </div>
                    <Check v-if="step.status === 'success'" :size="13" />
                  </li>
                </ol>
                <p v-else>还没有工具调用。</p>
                <p>
                  订单与身份均为虚构数据；对话保存在本项目，演示买家可自由切换。
                </p>
              </div>
            </details>
          </div>
        </aside>
      </div>
    </main>
  </div>
</template>
