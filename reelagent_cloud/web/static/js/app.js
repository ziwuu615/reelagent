/* ==========================================================================
   ReelAgent 全站脚本
   - CSP 为 script-src 'self'，因此不存在任何内联 <script>，全部逻辑集中在这里
   - 通过 body / data-* 属性做能力探测，同一份脚本服务所有页面
   ========================================================================== */
(function () {
  'use strict';

  var STAGES = {
    queued: '排队中',
    planning: '拆解剧本',
    assets: '生成角色与场景',
    generating: '逐镜生成',
    evaluating: '质量评测',
    rendering: '合成成片',
    done: '完成'
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  /* ------------------------------------------------------------------ */
  /* CSRF：双提交 Cookie。优先读 <meta>，其次读 Cookie（两者应始终一致）。   */
  /* ------------------------------------------------------------------ */
  function readCookie(name) {
    var parts = document.cookie ? document.cookie.split('; ') : [];
    for (var i = 0; i < parts.length; i++) {
      var idx = parts[i].indexOf('=');
      if (idx > -1 && parts[i].slice(0, idx) === name) {
        return decodeURIComponent(parts[i].slice(idx + 1));
      }
    }
    return '';
  }

  function csrfToken() {
    var meta = $('meta[name="csrf-token"]');
    var value = meta ? (meta.getAttribute('content') || '') : '';
    if (value) { return value; }
    var cookieName = $('meta[name="csrf-cookie"]');
    return cookieName ? readCookie(cookieName.getAttribute('content')) : '';
  }

  function csrfHeader() {
    var meta = $('meta[name="csrf-header"]');
    return meta && meta.getAttribute('content') ? meta.getAttribute('content') : 'x-csrf-token';
  }

  /** 统一 fetch：自动带 CSRF 头、Cookie 与 JSON 解析。 */
  function apiFetch(url, options) {
    var opts = options || {};
    var headers = opts.headers || {};
    headers[csrfHeader()] = csrfToken();
    if (opts.body && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    opts.headers = headers;
    opts.credentials = 'same-origin';
    return fetch(url, opts).then(function (res) {
      if (!res.ok) {
        var err = new Error('HTTP ' + res.status);
        err.status = res.status;
        throw err;
      }
      return res.json();
    });
  }

  /* ------------------------------------------------------------------ */
  /* 提示条：可关闭 + 自动淡出                                            */
  /* ------------------------------------------------------------------ */
  var AUTO_DISMISS_MS = 7000;

  function dismiss(alert) {
    if (!alert || alert.dataset.closing === '1') { return; }
    alert.dataset.closing = '1';
    alert.style.transition = 'opacity .25s, transform .25s';
    alert.style.opacity = '0';
    alert.style.transform = 'translateY(-4px)';
    window.setTimeout(function () {
      if (alert.parentNode) { alert.parentNode.removeChild(alert); }
    }, 260);
  }

  function initFlash() {
    $$('[data-flash]').forEach(function (alert, index) {
      var close = $('[data-dismiss]', alert);
      if (close) {
        close.addEventListener('click', function () { dismiss(alert); });
      }
      // 只自动关闭较早的提示，最新的留给用户看清
      if (index > 0) {
        window.setTimeout(function () { dismiss(alert); }, AUTO_DISMISS_MS);
      }
    });
  }

  /* ------------------------------------------------------------------ */
  /* 复制到剪贴板（API Key、分享链接等）                                   */
  /* ------------------------------------------------------------------ */
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    // 非安全上下文（如局域网 http）下的兜底实现
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', 'readonly');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy') ? resolve() : reject(new Error('copy failed'));
      } catch (e) {
        reject(e);
      } finally {
        document.body.removeChild(ta);
      }
    });
  }

  function initCopy() {
    $$('[data-copy]').forEach(function (btn) {
      var original = btn.getAttribute('data-copy-label') || btn.textContent.trim();
      btn.addEventListener('click', function () {
        var target = btn.getAttribute('data-copy');
        var source = target ? $(target) : null;
        var text = source ? (source.value !== undefined ? source.value : source.textContent) : '';
        text = (text || '').trim();
        if (!text) { return; }
        copyText(text).then(function () {
          btn.textContent = '已复制';
          btn.classList.add('is-copied');
          window.setTimeout(function () {
            btn.textContent = original;
            btn.classList.remove('is-copied');
          }, 1600);
        }).catch(function () {
          btn.textContent = '复制失败';
          window.setTimeout(function () { btn.textContent = original; }, 1600);
        });
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 危险操作确认：表单 data-confirm，按钮 data-confirm（表单内按钮优先）    */
  /* ------------------------------------------------------------------ */
  function initConfirm() {
    $$('form[data-confirm]').forEach(function (form) {
      form.addEventListener('submit', function (event) {
        if (form.dataset.confirmed === '1') { return; }
        var message = form.getAttribute('data-confirm') || '确认执行该操作？';
        if (!window.confirm(message)) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        form.dataset.confirmed = '1';
      });
    });

    $$('button[data-confirm]').forEach(function (btn) {
      btn.addEventListener('click', function (event) {
        var message = btn.getAttribute('data-confirm') || '确认执行该操作？';
        if (!window.confirm(message)) {
          event.preventDefault();
          event.stopPropagation();
        }
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 密码可见性切换                                                      */
  /* ------------------------------------------------------------------ */
  function initPasswordToggles() {
    $$('[data-toggle-password]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var field = $(btn.getAttribute('data-toggle-password'));
        if (!field) { return; }
        var show = field.getAttribute('type') === 'password';
        field.setAttribute('type', show ? 'text' : 'password');
        btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        btn.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 新建页：示例素材一键填入、素材字数统计、镜头额度预估                   */
  /* ------------------------------------------------------------------ */
  function initProjectNew() {
    var seed = $('#seed_text');
    if (!seed) { return; }

    var counter = $('[data-seed-count]');
    function updateCount() {
      if (counter) { counter.textContent = String(seed.value.length); }
    }
    seed.addEventListener('input', updateCount);
    updateCount();

    $$('[data-fill-seed]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        seed.value = btn.getAttribute('data-fill-seed') || '';
        seed.dispatchEvent(new Event('input', { bubbles: true }));
        seed.focus();
      });
    });

    // 额度预估：按镜头数线性缩放（路由已给出单次基准预估）
    var quoteNode = $('[data-quote]');
    var shotsNode = $('#shots');
    if (!quoteNode || !shotsNode) { return; }

    var baseShots = parseInt(quoteNode.getAttribute('data-quote-shots') || '0', 10);
    var baseCredits = parseInt(quoteNode.getAttribute('data-quote-credits') || '0', 10);
    var baseUsd = parseFloat(quoteNode.getAttribute('data-quote-usd') || '0');
    var balance = parseInt(quoteNode.getAttribute('data-quote-balance') || '0', 10);
    var outCredits = $('[data-quote-credits-out]');
    var outUsd = $('[data-quote-usd-out]');
    var outWarn = $('[data-quote-warn]');

    function refreshQuote() {
      var shots = parseInt(shotsNode.value, 10) || baseShots || 1;
      var ratio = baseShots ? shots / baseShots : 1;
      var credits = Math.max(1, Math.round(baseCredits * ratio));
      var usd = baseUsd * ratio;
      if (outCredits) { outCredits.textContent = credits.toLocaleString('zh-CN'); }
      if (outUsd) { outUsd.textContent = '$' + usd.toFixed(3); }
      if (outWarn) {
        outWarn.classList.toggle('hide', !(balance > 0 && credits > balance));
      }
    }

    shotsNode.addEventListener('change', refreshQuote);
    refreshQuote();
  }

  /* ------------------------------------------------------------------ */
  /* 工作室：镜头勾选 -> 隐藏字段，并同步「重跑选中镜头」按钮状态与费用       */
  /* ------------------------------------------------------------------ */
  function initStudioSelection() {
    var boxes = $$('[data-shot-check]');
    if (!boxes.length) { return; }

    var hidden = $('#selected_shot_keys');
    var summary = $('[data-selection-count]');
    var submit = $('[data-selection-submit]');
    var unit = parseInt((summary && summary.getAttribute('data-unit-credits')) || '0', 10);

    function sync() {
      var picked = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
      if (hidden) { hidden.value = picked.join(','); }
      if (summary) {
        var text = '已选 ' + picked.length + ' 个镜头';
        if (unit > 0 && picked.length) { text += ' · 预计消耗 ' + (unit * picked.length) + ' 点'; }
        summary.textContent = text;
      }
      if (submit) { submit.disabled = picked.length === 0; }
      $$('[data-shot-card]').forEach(function (card) {
        var box = $('[data-shot-check]', card);
        card.classList.toggle('is-selected', !!(box && box.checked));
      });
    }

    boxes.forEach(function (box) { box.addEventListener('change', sync); });
    $$('[data-select-all]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var mode = btn.getAttribute('data-select-all');
        boxes.forEach(function (b) { b.checked = mode !== 'none'; });
        sync();
      });
    });
    sync();
  }

  /* ------------------------------------------------------------------ */
  /* 进度轮询：GET /api/v1/jobs/{id}，完成后刷新页面以拿到成片             */
  /* ------------------------------------------------------------------ */
  function renderJob(wrap, job) {
    var pct = job.status === 'failed' || job.status === 'dead' || job.status === 'cancelled'
      ? 0
      : Math.round((job.progress || 0) * 1000) / 10;

    var bar = $('[data-job-fill]', wrap);
    var percent = $('[data-job-percent]', wrap);
    var stage = $('[data-job-stage]', wrap);
    var track = $('[data-job-bar]', wrap);

    if (bar) {
      bar.style.width = pct + '%';
      bar.className = 'progress-fill tone-' + toneFor(job.status);
    }
    if (percent) { percent.textContent = String(pct); }
    if (stage) { stage.textContent = STAGES[job.stage] || job.stage; }
    if (track) { track.setAttribute('aria-valuenow', String(pct)); }

    var order = ['queued', 'planning', 'assets', 'generating', 'evaluating', 'rendering', 'done'];
    var current = order.indexOf(job.stage);
    var broken = job.status === 'failed' || job.status === 'dead' || job.status === 'cancelled';
    $$('.stages .stage', wrap).forEach(function (li, index) {
      li.classList.toggle('is-done', !broken && current > -1 && index < current);
      li.classList.toggle('is-current', !broken && index === current);
    });

    // 阶段说明右侧的状态徽章同步
    var badge = $('.card-head .badge', wrap);
    if (badge) {
      var label = statusLabel(job.status);
      badge.className = 'badge badge-' + toneFor(job.status);
      badge.innerHTML = '<i class="dot"></i>' + label;
    }
  }

  function toneFor(status) {
    if (status === 'succeeded') { return 'ok'; }
    if (status === 'running' || status === 'pending') { return 'busy'; }
    if (status === 'failed' || status === 'dead') { return 'bad'; }
    if (status === 'cancelled') { return 'muted'; }
    return 'neutral';
  }

  function statusLabel(status) {
    return {
      pending: '排队中', running: '生成中', succeeded: '成功',
      failed: '失败', dead: '失败', cancelled: '已取消'
    }[status] || status;
  }

  function initJobPolling() {
    $$('[data-job-progress]').forEach(function (wrap) {
      var jobId = wrap.getAttribute('data-job-progress');
      if (!jobId) { return; }
      var interval = parseInt(wrap.getAttribute('data-poll-ms') || '4000', 10);
      var failures = 0;
      var stopped = false;

      function tick() {
        if (stopped) { return; }
        apiFetch('/api/v1/jobs/' + encodeURIComponent(jobId), { method: 'GET' })
          .then(function (job) {
            failures = 0;
            renderJob(wrap, job);
            var terminal = ['succeeded', 'failed', 'dead', 'cancelled'].indexOf(job.status) > -1;
            if (terminal) {
              stopped = true;
              // 生成完成或失败后刷新，服务端渲染出成片 / 错误详情
              window.setTimeout(function () { window.location.reload(); }, 1200);
              return;
            }
            window.setTimeout(tick, interval);
          })
          .catch(function (err) {
            failures += 1;
            // 404 通常意味着任务已被清理；401/403 表示会话过期，都不必再轮询
            if (err.status === 404 || err.status === 401 || err.status === 403) {
              stopped = true;
              return;
            }
            // 网络抖动：退避重试，不做无限刷新
            if (failures <= 5) {
              window.setTimeout(tick, Math.min(interval * failures, 20000));
            }
          });
      }

      window.setTimeout(tick, interval);
    });
  }

  /* ------------------------------------------------------------------ */
  /* 项目列表：筛选 / 搜索表单自动提交                                     */
  /* ------------------------------------------------------------------ */
  function initAutoSubmit() {
    $$('[data-auto-submit]').forEach(function (field) {
      field.addEventListener('change', function () {
        var form = field.form;
        if (form) { form.submit(); }
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 提交中的按钮防重复点击                                                */
  /* ------------------------------------------------------------------ */
  function initSubmitLock() {
    $$('form[data-lock]').forEach(function (form) {
      form.addEventListener('submit', function () {
        var btn = $('button[type="submit"]', form);
        if (!btn) { return; }
        if (form.dataset.confirmed !== '1' && form.hasAttribute('data-confirm')) { return; }
        window.setTimeout(function () {
          btn.disabled = true;
          btn.textContent = '处理中…';
        }, 0);
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* 通用弹窗：data-dialog-open="#id" 打开，data-dialog-close 关闭          */
  /* 触发元素可用 data-fill-* 把值写进弹窗内的同名 [data-dialog-field]      */
  /* ------------------------------------------------------------------ */
  function initDialogs() {
    function close(dialog) {
      if (!dialog) { return; }
      if (typeof dialog.close === 'function' && dialog.open) {
        dialog.close();
      } else {
        dialog.removeAttribute('open');
      }
    }

    $$('[data-dialog-open]').forEach(function (trigger) {
      trigger.addEventListener('click', function (event) {
        event.preventDefault();
        var dialog = $(trigger.getAttribute('data-dialog-open'));
        if (!dialog) { return; }

        // 把触发器上的 data-fill-xxx 写入弹窗内对应的 [data-dialog-field="xxx"]
        Array.prototype.slice.call(trigger.attributes).forEach(function (attr) {
          if (attr.name.indexOf('data-fill-') !== 0) { return; }
          var key = attr.name.slice('data-fill-'.length);
          var field = $('[data-dialog-field="' + key + '"]', dialog);
          if (!field) { return; }
          if (field.tagName === 'FORM') {
            field.setAttribute('action', attr.value);
          } else if (field.tagName === 'A') {
            field.setAttribute('href', attr.value);
          } else {
            field.value = attr.value;
          }
        });

        var text = $('[data-dialog-text]', dialog);
        var label = trigger.getAttribute('data-dialog-title');
        if (text && label) { text.textContent = label; }
        var image = $('[data-dialog-image]', dialog);
        if (image) {
          var src = trigger.getAttribute('data-fill-image') || '';
          image.classList.toggle('hide', !src);
          if (src) { image.setAttribute('src', src); }
        }

        if (typeof dialog.showModal === 'function') {
          dialog.showModal();
        } else {
          dialog.setAttribute('open', 'open');
        }
      });
    });

    $$('[data-dialog-close]').forEach(function (btn) {
      btn.addEventListener('click', function (event) {
        event.preventDefault();
        close(btn.closest('dialog'));
      });
    });

    // 点击遮罩区域关闭
    $$('dialog.sheet').forEach(function (dialog) {
      dialog.addEventListener('click', function (event) {
        if (event.target === dialog) { close(dialog); }
      });
    });
  }

  /* ------------------------------------------------------------------ */
  document.addEventListener('DOMContentLoaded', function () {
    initFlash();
    initCopy();
    initConfirm();
    initPasswordToggles();
    initProjectNew();
    initStudioSelection();
    initDialogs();
    initJobPolling();
    initAutoSubmit();
    initSubmitLock();
  });
})();
