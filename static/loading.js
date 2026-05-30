(function () {
  if (window.__gwangleLoadingInstalled) {
    return;
  }

  window.__gwangleLoadingInstalled = true;

  const LOADING_WORDS = [
    "불러오는 중",
    "전송 중",
    "답변을 생성하는 중",
    "시작 중",
    "저장하는 중",
    "처리 중",
    "검색 중",
    "로딩 중",
    "이동 중"
  ];

  const SKIP_FETCH_KEYWORDS = [
    "/api/suggest",
    "/api/game/leaderboard"
  ];

  let activeFetchCount = 0;
  let overlayShowTimer = null;
  let overlayHideTimer = null;
  let overlayMaxTimer = null;
  let dotIndex = 0;

  function injectLoadingStyle() {
    if (document.getElementById("gwangle-loading-style")) {
      return;
    }

    const style = document.createElement("style");
    style.id = "gwangle-loading-style";

    style.textContent = `
      #gwanglePageLoader {
        position: fixed;
        inset: 0;
        z-index: 999999;
        display: none;
        align-items: center;
        justify-content: center;
        background: rgba(255, 255, 255, 0.78);
        backdrop-filter: blur(6px);
      }

      #gwanglePageLoader.show {
        display: flex;
      }

      .gwangle-loader-box {
        min-width: 190px;
        padding: 24px 26px;
        border-radius: 24px;
        background: #ffffff;
        border: 1px solid #dadce0;
        box-shadow: 0 8px 30px rgba(60, 64, 67, 0.18);
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 14px;
      }

      .gwangle-spinner {
        width: 42px;
        height: 42px;
        border-radius: 50%;
        border: 4px solid #e8eaed;
        border-top-color: #1a73e8;
        animation: gwangleSpin 0.85s linear infinite;
      }

      .gwangle-loader-text {
        color: #202124;
        font-size: 15px;
        font-weight: 700;
      }

      @keyframes gwangleSpin {
        from {
          transform: rotate(0deg);
        }
        to {
          transform: rotate(360deg);
        }
      }

      .gwangle-inline-loading {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: #5f6368;
        font-weight: 600;
      }

      .gwangle-inline-loading::before {
        content: "";
        width: 14px;
        height: 14px;
        border-radius: 50%;
        border: 2px solid #dadce0;
        border-top-color: #1a73e8;
        animation: gwangleSpin 0.85s linear infinite;
        flex: 0 0 auto;
      }
    `;

    document.head.appendChild(style);
  }

  function ensureOverlay() {
    let overlay = document.getElementById("gwanglePageLoader");

    if (overlay) {
      return overlay;
    }

    overlay = document.createElement("div");
    overlay.id = "gwanglePageLoader";

    overlay.innerHTML = `
      <div class="gwangle-loader-box">
        <div class="gwangle-spinner"></div>
        <div class="gwangle-loader-text">
          <span id="gwangleLoaderBaseText">로딩 중</span><span id="gwangleLoaderDots">.</span>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    return overlay;
  }

  function showGlobalLoader(message, maxMs) {
    injectLoadingStyle();

    const overlay = ensureOverlay();
    const baseText = document.getElementById("gwangleLoaderBaseText");

    if (baseText) {
      baseText.textContent = message || "로딩 중";
    }

    clearTimeout(overlayShowTimer);
    clearTimeout(overlayHideTimer);
    clearTimeout(overlayMaxTimer);

    overlay.classList.add("show");

    overlayMaxTimer = setTimeout(() => {
      forceHideGlobalLoader();
    }, maxMs || 6000);
  }

  function scheduleGlobalLoader(message, delay, maxMs) {
    clearTimeout(overlayShowTimer);

    overlayShowTimer = setTimeout(() => {
      showGlobalLoader(message, maxMs);
    }, delay || 250);
  }

  function hideGlobalLoader() {
    const overlay = document.getElementById("gwanglePageLoader");

    clearTimeout(overlayShowTimer);
    clearTimeout(overlayMaxTimer);

    if (!overlay) {
      return;
    }

    clearTimeout(overlayHideTimer);

    overlayHideTimer = setTimeout(() => {
      overlay.classList.remove("show");
    }, 120);
  }

  function forceHideGlobalLoader() {
    const overlay = document.getElementById("gwanglePageLoader");

    clearTimeout(overlayShowTimer);
    clearTimeout(overlayHideTimer);
    clearTimeout(overlayMaxTimer);

    activeFetchCount = 0;

    if (overlay) {
      overlay.classList.remove("show");
    }
  }

  function shouldSkipFetch(url) {
    return SKIP_FETCH_KEYWORDS.some(keyword => url.includes(keyword));
  }

  function installFetchLoader() {
    if (!window.fetch) {
      return;
    }

    const originalFetch = window.fetch;

    window.fetch = async function () {
      const args = arguments;
      const firstArg = args[0];

      let url = "";

      if (typeof firstArg === "string") {
        url = firstArg;
      } else if (firstArg && firstArg.url) {
        url = firstArg.url;
      }

      if (shouldSkipFetch(url)) {
        return originalFetch.apply(this, args);
      }

      activeFetchCount += 1;

      const fetchTimer = setTimeout(() => {
        if (activeFetchCount > 0) {
          showGlobalLoader("불러오는 중", 10000);
        }
      }, 450);

      try {
        return await originalFetch.apply(this, args);
      } finally {
        clearTimeout(fetchTimer);

        activeFetchCount = Math.max(0, activeFetchCount - 1);

        if (activeFetchCount === 0) {
          hideGlobalLoader();
        }
      }
    };
  }

  function isSamePageLink(link) {
    const href = link.getAttribute("href") || "";

    if (!href || href === "#") {
      return true;
    }

    if (href.startsWith("#")) {
      return true;
    }

    if (href.startsWith("javascript:")) {
      return true;
    }

    try {
      const linkUrl = new URL(link.href, window.location.href);

      return (
        linkUrl.origin === window.location.origin &&
        linkUrl.pathname === window.location.pathname &&
        linkUrl.search === window.location.search &&
        linkUrl.hash
      );
    } catch (error) {
      return false;
    }
  }

  function installNavigationLoader() {
    document.addEventListener("click", function (event) {
      const link = event.target.closest("a");

      if (link) {
        if (
          !isSamePageLink(link) &&
          link.target !== "_blank" &&
          !event.ctrlKey &&
          !event.metaKey &&
          !event.shiftKey &&
          !event.altKey
        ) {
          scheduleGlobalLoader("이동 중", 250, 2500);
        }

        return;
      }

      const button = event.target.closest("button");

      if (button) {
        const onclickText = button.getAttribute("onclick") || "";

        if (
          onclickText.includes("location.href") ||
          onclickText.includes("location.replace") ||
          onclickText.includes("location.assign")
        ) {
          scheduleGlobalLoader("이동 중", 250, 2500);
        }
      }
    }, true);

    document.addEventListener("submit", function () {
      showGlobalLoader("처리 중", 8000);
    }, true);

    window.addEventListener("pageshow", function () {
      forceHideGlobalLoader();
      cleanupInlineLoadingElements();
    });

    window.addEventListener("load", function () {
      forceHideGlobalLoader();
      cleanupInlineLoadingElements();
    });

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        forceHideGlobalLoader();
        cleanupInlineLoadingElements();
      }
    });

    window.addEventListener("focus", function () {
      forceHideGlobalLoader();
      cleanupInlineLoadingElements();
    });

    setTimeout(() => {
      forceHideGlobalLoader();
      cleanupInlineLoadingElements();
    }, 300);

    setTimeout(() => {
      forceHideGlobalLoader();
      cleanupInlineLoadingElements();
    }, 1200);
  }

  function isLoadingText(text) {
    const normalized = normalizeLoadingText(text);
    return LOADING_WORDS.some(word => normalized.includes(word));
  }

  function normalizeLoadingText(text) {
    return String(text || "")
      .replace(/[.\s…]+$/g, "")
      .trim();
  }

  function shouldIgnoreInlineElement(element) {
    if (!element) {
      return true;
    }

    if (
      element.id === "gwangleLoaderBaseText" ||
      element.id === "gwangleLoaderDots" ||
      element.closest("#gwanglePageLoader")
    ) {
      return true;
    }

    const tag = element.tagName.toLowerCase();

    if (
      tag === "button" ||
      tag === "input" ||
      tag === "textarea" ||
      tag === "select" ||
      tag === "a"
    ) {
      return true;
    }

    if (element.children.length > 0) {
      return true;
    }

    return false;
  }

  function cleanupOneInlineElement(element) {
    if (!element.classList.contains("gwangle-inline-loading")) {
      return;
    }

    const currentText = element.textContent.trim();
    const baseText = element.dataset.gwangleLoadingBase || "";

    if (!isLoadingText(currentText) && !isLoadingText(baseText)) {
      element.classList.remove("gwangle-inline-loading");
      delete element.dataset.gwangleLoadingBase;
      return;
    }

    if (!isLoadingText(currentText) && isLoadingText(baseText)) {
      element.classList.remove("gwangle-inline-loading");
      delete element.dataset.gwangleLoadingBase;
    }
  }

  function cleanupInlineLoadingElements() {
    if (!document.body) {
      return;
    }

    document.querySelectorAll(".gwangle-inline-loading").forEach(element => {
      const tag = element.tagName.toLowerCase();

      if (
        tag === "button" ||
        tag === "input" ||
        tag === "textarea" ||
        tag === "select" ||
        tag === "a"
      ) {
        element.classList.remove("gwangle-inline-loading");
        delete element.dataset.gwangleLoadingBase;
        return;
      }

      cleanupOneInlineElement(element);
    });
  }

  function scanInlineLoadingElements() {
    if (!document.body) {
      return;
    }

    cleanupInlineLoadingElements();

    const elements = document.body.querySelectorAll(
      "div, span, p, h1, h2, h3, td"
    );

    elements.forEach(element => {
      if (shouldIgnoreInlineElement(element)) {
        return;
      }

      const text = element.textContent.trim();

      if (!text || text.length > 80) {
        return;
      }

      if (!isLoadingText(text)) {
        if (element.classList.contains("gwangle-inline-loading")) {
          element.classList.remove("gwangle-inline-loading");
          delete element.dataset.gwangleLoadingBase;
        }
        return;
      }

      if (!element.dataset.gwangleLoadingBase) {
        element.dataset.gwangleLoadingBase = normalizeLoadingText(text);
      }

      element.classList.add("gwangle-inline-loading");
    });
  }

  function animateDots() {
    dotIndex = (dotIndex + 1) % 3;
    const dots = ".".repeat(dotIndex + 1);

    const overlayDots = document.getElementById("gwangleLoaderDots");

    if (overlayDots) {
      overlayDots.textContent = dots;
    }

    document.querySelectorAll(".gwangle-inline-loading").forEach(element => {
      if (shouldIgnoreInlineElement(element)) {
        element.classList.remove("gwangle-inline-loading");
        delete element.dataset.gwangleLoadingBase;
        return;
      }

      const base = element.dataset.gwangleLoadingBase;

      if (!base || !isLoadingText(base)) {
        element.classList.remove("gwangle-inline-loading");
        delete element.dataset.gwangleLoadingBase;
        return;
      }

      element.textContent = `${base}${dots}`;
    });
  }

  function installInlineLoadingObserver() {
    scanInlineLoadingElements();

    const observer = new MutationObserver(() => {
      scanInlineLoadingElements();
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true
    });

    setInterval(animateDots, 420);
  }

  function init() {
    injectLoadingStyle();
    ensureOverlay();
    forceHideGlobalLoader();

    installFetchLoader();
    installNavigationLoader();
    installInlineLoadingObserver();

    setTimeout(forceHideGlobalLoader, 300);
    setTimeout(forceHideGlobalLoader, 1000);
    setTimeout(forceHideGlobalLoader, 2500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();