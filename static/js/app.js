const { createApp, ref, reactive, computed, nextTick, onMounted, onUnmounted } = Vue
createApp({
  setup() {
    const searchId = ref('')
    const matches = ref([])
    const total = ref(0)
    const loading = ref(false)
    const searched = ref(false)
    const progress = ref(0)
    const popupVisible = ref(false)
    const activeMatch = reactive({})
    const iframeLoaded = ref(false)
    const logsEl = ref(null)
    const logsDrawerVisible = ref(false)
    const allLogs = ref([])
    const logBatches = ref([])
    const logsLoading = ref(false)
    const logFilters = reactive({ batch_id: '', type: 'all', date_from: '', date_to: '' })

    const now = new Date()
    const yesterday = new Date(now.getTime() - 86400000)
    const dateMin = '1900-01-01'
    const yesterdayStr = yesterday.getFullYear() + '-' + String(yesterday.getMonth() + 1).padStart(2, '0') + '-' + String(yesterday.getDate()).padStart(2, '0')
    const dateMax = yesterdayStr

    const crawlStartDate = ref(yesterdayStr)
    const crawlEndDate = ref(yesterdayStr)
    const crawlDateMin = dateMin
    const crawlDateMax = dateMax
    const queryDate = ref(yesterdayStr)

    const crawl = reactive({
      running: false, progress: 0, total: 0, current: 0,
      current_match_id: '', current_match_name: '',
      qualified: 0, skipped: 0, failed: 0, finished: false, error: null, logs: [], recent_logs: [],
      crawl_date: null, batch_id: null, start_date: null, end_date: null,
      day_index: 0, day_total: 0, batch_summary: { total_days: 0, finished_days: 0, success_days: 0, failed_days: 0, total_matches: 0, qualified_matches: 0, skipped_matches: 0, failed_matches: 0 },
    })

    let crawlPollTimer = null
    let dataRefreshTimer = null
    const logFilter = ref('all')
    const selectedLogBatchId = ref('')
    const selectedLogDateFrom = ref('')
    const selectedLogDateTo = ref('')

    function simulateProgress() {
      progress.value = 0
      let p = 0
      const iv = setInterval(() => {
        if (p >= 90) { clearInterval(iv); return }
        p += Math.random() * 15
        if (p > 90) p = 90
        progress.value = Math.round(p)
      }, 200)
      return iv
    }

    async function fetchMatches(params = '') {
      loading.value = true
      searched.value = true
      stopDataRefresh()
      const iv = simulateProgress()
      try {
        const resp = await fetch('/api/matches' + params)
        const data = await resp.json()
        matches.value = data.matches
        total.value = data.total
        if (data.query_date && params === '') {
          const d = data.query_date
          queryDate.value = d.substring(0, 4) + '-' + d.substring(4, 6) + '-' + d.substring(6, 8)
        }
        progress.value = 100
      } finally {
        clearInterval(iv)
        progress.value = 100
        setTimeout(() => { loading.value = false; progress.value = 0 }, 400)
      }
    }

    function searchById() { const id = searchId.value.trim(); if (id) fetchMatches('?match_id=' + encodeURIComponent(id)) }
    function loadByDate() { searchId.value = ''; fetchMatches('?date=' + queryDate.value.replace(/-/g, '')) }
    function onQueryDateChange() {}

    async function loadAllLogs() {
      logsLoading.value = true
      try {
        const qs = new URLSearchParams()
        if (selectedLogBatchId.value) qs.set('batch_id', selectedLogBatchId.value)
        if (logFilters.type && logFilters.type !== 'all') qs.set('type', logFilters.type)
        if (selectedLogDateFrom.value) qs.set('date_from', selectedLogDateFrom.value)
        if (selectedLogDateTo.value) qs.set('date_to', selectedLogDateTo.value)
        const resp = await fetch('/api/logs' + (qs.toString() ? '?' + qs.toString() : ''))
        const data = await resp.json()
        allLogs.value = data.logs || []
      } finally {
        logsLoading.value = false
        refreshLogPanel()
      }
    }

    async function loadLogBatches() {
      const resp = await fetch('/api/logs/batches')
      const data = await resp.json()
      logBatches.value = data.batches || []
    }

    async function openLogsDrawer() {
      logsDrawerVisible.value = true
      await Promise.all([loadLogBatches(), loadAllLogs()])
      nextTick(() => { if (logsEl.value) logsEl.value.scrollTop = logsEl.value.scrollHeight })
    }
    function closeLogsDrawer() { logsDrawerVisible.value = false }
    function formatDisplayDate(dateStr) { if (!dateStr || dateStr.length !== 8) return dateStr || ''; return dateStr.substring(0, 4) + '-' + dateStr.substring(4, 6) + '-' + dateStr.substring(6, 8) }

    const panelTitle = computed(() => crawl.error ? '❌ 爬取出错' : crawl.running ? '🔄 正在爬取...' : crawl.finished ? '✅ 爬取完成' : (crawl.batch_summary.total_days > 0 && crawl.batch_summary.finished_days < crawl.batch_summary.total_days) ? '⏸ 批次已暂停' : '⏸ 爬取已暂停')
    const isStoppedState = computed(() => !crawl.running && !crawl.finished && !crawl.error && (crawl.batch_summary.total_days > 0 || crawl.day_index > 0 || crawl.current > 0))
    const showCrawlPanel = computed(() => crawl.running || crawl.finished || crawl.error || isStoppedState.value || crawl.batch_summary.total_days > 0)
    const currentQueryLabel = computed(() => formatDisplayDate(queryDate.value.replace(/-/g, '')))
    const batchProgress = computed(() => { const total = crawl.batch_summary.total_days || crawl.day_total || 0; const finished = crawl.batch_summary.finished_days || 0; return total ? Math.min(100, Math.round(finished / total * 100)) : 0 })
    const displayedLogs = computed(() => allLogs.value)
    const filteredLogs = computed(() => displayedLogs.value.filter(log => { if (logFilters.batch_id && log.batch_id !== logFilters.batch_id) return false; if (logFilters.type !== 'all' && log.type !== logFilters.type) return false; if (logFilters.date_from && log.time.slice(0, 10) < logFilters.date_from) return false; if (logFilters.date_to && log.time.slice(0, 10) > logFilters.date_to) return false; return true }))
    function refreshLogPanel() { nextTick(() => { if (logsEl.value) logsEl.value.scrollTop = logsEl.value.scrollHeight }) }
    async function refreshLogs() { await loadAllLogs() }

    function startDataRefresh() { stopDataRefresh(); if (!crawl.running || !crawl.crawl_date) return; dataRefreshTimer = setInterval(() => { fetch('/api/matches?date=' + crawl.crawl_date).then(r => r.json()).then(data => { matches.value = data.matches; total.value = data.total }) }, 2000) }
    function stopDataRefresh() { if (dataRefreshTimer) { clearInterval(dataRefreshTimer); dataRefreshTimer = null } }

    async function startCrawl() {
      const startDate = crawlStartDate.value.replace(/-/g, '')
      const endDate = crawlEndDate.value.replace(/-/g, '')
      if (!startDate || !endDate) return alert('请选择开始日期和结束日期')
      const resp = await fetch('/api/crawl/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start_date: startDate, end_date: endDate }) })
      if (resp.status === 409) return alert('爬取正在进行中，请等待完成')
      const result = await resp.json()
      Object.assign(crawl, { running: true, progress: 0, total: 0, current: 0, current_match_id: '', current_match_name: '', qualified: 0, skipped: 0, failed: 0, finished: false, error: null, logs: [], recent_logs: [], crawl_date: result.start_date, batch_id: null, start_date: result.start_date, end_date: result.end_date, day_index: 0, day_total: 0, batch_summary: { total_days: 0, finished_days: 0, success_days: 0, failed_days: 0, total_matches: 0, qualified_matches: 0, skipped_matches: 0, failed_matches: 0 } })
      startCrawlPoll(); startDataRefresh()
    }
    async function stopCrawl() { await fetch('/api/crawl/stop', { method: 'POST' }) }
    async function resumeCrawl() { const startDate = crawl.start_date || crawl.crawl_date; const endDate = crawl.end_date || crawl.crawl_date; const resp = await fetch('/api/crawl/resume', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start_date: startDate, end_date: endDate }) }); if (resp.status === 409) return alert('爬取正在进行中'); Object.assign(crawl, { running: true, progress: 0, total: 0, current: 0, current_match_id: '', current_match_name: '', qualified: 0, skipped: 0, failed: 0, finished: false, error: null, logs: [], recent_logs: [], crawl_date: startDate, batch_id: null, start_date: startDate, end_date: endDate, day_index: 0, day_total: 0 }); startCrawlPoll(); startDataRefresh() }
    async function closeCrawl() { await fetch('/api/crawl/close', { method: 'POST' }); Object.assign(crawl, { running: false, progress: 0, total: 0, current: 0, current_match_id: '', current_match_name: '', qualified: 0, skipped: 0, failed: 0, finished: false, error: null, logs: [], recent_logs: [], crawl_date: null, batch_id: null, start_date: null, end_date: null, day_index: 0, day_total: 0, batch_summary: { total_days: 0, finished_days: 0, success_days: 0, failed_days: 0, total_matches: 0, qualified_matches: 0, skipped_matches: 0, failed_matches: 0 } }); stopCrawlPoll(); stopDataRefresh(); loadByDate() }
    function dismissCrawl() { closeCrawl() }
    function startCrawlPoll() { stopCrawlPoll(); crawlPollTimer = setInterval(async () => { try { const resp = await fetch('/api/crawl/status'); const data = await resp.json(); Object.assign(crawl, data); if (!crawl.recent_logs || !crawl.recent_logs.length) crawl.recent_logs = data.recent_logs || []; if (logsDrawerVisible.value) await refreshLogs(); else refreshLogPanel(); if ((data.finished || data.error) && !data.running) { stopCrawlPoll(); stopDataRefresh(); loadByDate() } } catch (e) { console.error(e) } }, 1000) }
    function stopCrawlPoll() { if (crawlPollTimer) { clearInterval(crawlPollTimer); crawlPollTimer = null } }

    onMounted(async () => { try { const resp = await fetch('/api/crawl/status'); const data = await resp.json(); if (data.running) { Object.assign(crawl, data); startCrawlPoll(); startDataRefresh() } else { loadByDate() } } catch (e) { loadByDate() } })
    onUnmounted(() => { stopCrawlPoll(); stopDataRefresh() })

    function openPopup(event, m) { Object.assign(activeMatch, m); iframeLoaded.value = false; popupVisible.value = true }
    function closePopup() { popupVisible.value = false; Object.keys(activeMatch).forEach(k => delete activeMatch[k]) }
    function proxyUrl(url) { return url ? url.replace('https://zq.titan007.com', '/proxy') : '' }
    function formatSummary(text) { if (!text) return ''; return text.replace(/胜(\d+)/g, '<span class="w">胜$1</span>').replace(/平(\d+)/g, '<span class="d">平$1</span>').replace(/负(\d+)/g, '<span class="l">负$1</span>') }

    return { searchId, matches, total, loading, searched, progress, popupVisible, activeMatch, iframeLoaded, logsEl, crawlStartDate, crawlEndDate, crawlDateMin, crawlDateMax, queryDate, dateMin, dateMax, crawl, searchById, loadByDate, onQueryDateChange, startCrawl, stopCrawl, resumeCrawl, closeCrawl, dismissCrawl, openLogsDrawer, closeLogsDrawer, logsDrawerVisible, loadAllLogs, loadLogBatches, refreshLogs, selectedLogBatchId, selectedLogDateFrom, selectedLogDateTo, logBatches, logsLoading, logFilters, filteredLogs, logFilter, openPopup, closePopup, proxyUrl, formatSummary, panelTitle, isStoppedState, showCrawlPanel, currentQueryLabel, formatDisplayDate }
  }
}).mount('#app')
