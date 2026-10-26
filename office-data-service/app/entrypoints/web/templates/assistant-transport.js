/* 仅同源BFF；超时不重放写入，错误沿用统一契约。 */
(() => {
  const root = document.getElementById('assistant-panel');
  if (!root) return;
  class RequestError extends Error {
    constructor(message, status, trace) {super(message); this.status = status; this.trace = trace;}
  }
  async function request(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/console/assistant-ui/' + path, {
        method: body ? 'POST' : 'GET', credentials:'same-origin', redirect:'error',
        headers: {'Accept':'application/json', ...(body ? {'Content-Type':'application/json'} : {})},
        ...(body ? {body: JSON.stringify(body)} : {}), signal: controller.signal,
      });
      if (!response.headers.get('content-type')?.includes('application/json')) throw new RequestError('登录或服务状态已变化，请刷新页面核对。', response.status);
      const result = await response.json();
      if (!response.ok || result.code !== 0) throw new RequestError(response.status===409 ? '此操作状态已变化或已处理，已刷新；请重新核对，不会重复执行。' : result.message || '请求未完成，请核对状态。', response.status, result.meta?.trace_id);
      return result.data;
    } catch (error) {
      if (error instanceof RequestError) throw error;
      throw new RequestError(body ? '请求回执未收到，请刷新核对实际状态；不会自动重试此操作。' : '暂时无法连接，请检查网络后重新加载。', 0);
    } finally {clearTimeout(timeout);}
  }
  window.OfficeAssistant = {
    root, RequestError,
    get: path => request(path),
    action: (action, payload) => request('action', {action, payload, csrf:root.dataset.csrf}),
  };
})();
