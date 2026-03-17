self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function parsePushPayload(event) {
  if (!event.data) {
    return {};
  }

  try {
    return event.data.json();
  } catch (error) {
    return { raw_text: event.data.text() };
  }
}

function buildNotificationDetails(payload) {
  const data = payload?.data || payload?.notification || payload || {};
  const title = data.title || "NODO";
  const body =
    data.body ||
    data.visible_status ||
    data.event_type ||
    "New NODO notification";

  return {
    title,
    options: {
      body,
      data,
    },
  };
}

self.addEventListener("push", (event) => {
  const payload = parsePushPayload(event);
  const { title, options } = buildNotificationDetails(payload);

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetUrl = event.notification.data?.url || "http://localhost:8000/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.startsWith(targetUrl) && "focus" in client) {
          return client.focus();
        }
      }

      if ("openWindow" in self.clients) {
        return self.clients.openWindow(targetUrl);
      }

      return undefined;
    })
  );
});
