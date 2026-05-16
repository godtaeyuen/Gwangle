const searchInput = document.getElementById("searchInput");
const autocompleteBox = document.getElementById("autocompleteBox");
const resultsArea = document.getElementById("resultsArea");
const results = document.getElementById("results");

let currentSuggestions = [];
let activeIndex = -1;

function searchData() {
  const query = searchInput.value.trim();

  if (!query) {
    resultsArea.style.display = "block";
    results.innerHTML = `<div class="empty-message">검색어를 입력해주세요.</div>`;
    hideSuggestions();
    return;
  }

  fetch(`/api/search?query=${encodeURIComponent(query)}`)
    .then(response => response.json())
    .then(data => {
      renderResults(data);
      hideSuggestions();
    })
    .catch(error => {
      console.error(error);
      resultsArea.style.display = "block";
      results.innerHTML = `<div class="empty-message">검색 중 오류가 발생했습니다.</div>`;
    });
}

function renderResults(list) {
  if (!list.length) {
    results.innerHTML = `<div class="empty-message">검색 결과가 없습니다.</div>`;
    resultsArea.style.display = "block";
    return;
  }

  results.innerHTML = list.map(item => `
    <div class="result-card">
      <div class="result-type">${item.type}</div>
      <div class="result-title">${item.title}</div>
      <div class="result-desc">${item.desc}</div>
      <div class="result-tags">
        ${(item.tags || []).map(tag => `<span class="tag">${tag}</span>`).join("")}
      </div>
    </div>
  `).join("");

  resultsArea.style.display = "block";
}

function resetSearch() {
  searchInput.value = "";
  resultsArea.style.display = "none";
  results.innerHTML = "";
  hideSuggestions();
  searchInput.focus();
}

function getSuggestions(query) {
  return fetch(`/api/suggest?query=${encodeURIComponent(query)}`)
    .then(response => response.json());
}

function renderSuggestions(list) {
  if (!list.length) {
    hideSuggestions();
    return;
  }

  autocompleteBox.innerHTML = list.map((item, index) => `
    <div class="suggestion-item ${index === activeIndex ? "active" : ""}" data-value="${item}">
      <span class="suggestion-icon">⌕</span>
      <span>${item}</span>
    </div>
  `).join("");

  autocompleteBox.style.display = "block";

  document.querySelectorAll(".suggestion-item").forEach(el => {
    el.addEventListener("click", () => {
      const value = el.getAttribute("data-value");
      searchInput.value = value;
      hideSuggestions();
      searchData();
    });
  });
}

function hideSuggestions() {
  autocompleteBox.style.display = "none";
  autocompleteBox.innerHTML = "";
  activeIndex = -1;
}

searchInput.addEventListener("input", async (e) => {
  const value = e.target.value.trim();
  if (!value) {
    hideSuggestions();
    return;
  }

  try {
    currentSuggestions = await getSuggestions(value);
    activeIndex = -1;
    renderSuggestions(currentSuggestions);
  } catch (error) {
    console.error(error);
    hideSuggestions();
  }
});

searchInput.addEventListener("keydown", (e) => {
  if (autocompleteBox.style.display === "block" && currentSuggestions.length > 0) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activeIndex = (activeIndex + 1) % currentSuggestions.length;
      renderSuggestions(currentSuggestions);
      return;
    }

    if (e.key === "ArrowUp") {
      e.preventDefault();
      activeIndex = (activeIndex - 1 + currentSuggestions.length) % currentSuggestions.length;
      renderSuggestions(currentSuggestions);
      return;
    }

    if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      searchInput.value = currentSuggestions[activeIndex];
      hideSuggestions();
      searchData();
      return;
    }

    if (e.key === "Escape") {
      hideSuggestions();
      return;
    }
  }

  if (e.key === "Enter") {
    searchData();
  }
});

document.addEventListener("click", (e) => {
  if (!autocompleteBox.contains(e.target) && e.target !== searchInput) {
    hideSuggestions();
  }
});