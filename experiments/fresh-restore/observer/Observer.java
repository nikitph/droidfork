package org.droidfork.observer;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Path;
import android.graphics.Rect;
import android.hardware.HardwareBuffer;
import java.net.ServerSocket;
import java.net.InetAddress;
import java.security.MessageDigest;
import java.net.Socket;
import android.os.Handler;
import android.os.Looper;

import android.os.SystemClock;
import android.util.Log;
import android.util.Xml;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.xmlpull.v1.XmlSerializer;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

public final class Observer extends AccessibilityService {
    private static final String CONTACTS = "com.google.android.contacts";
    private final Handler main = new Handler(Looper.getMainLooper());
    private volatile ServerSocket server;

    @Override protected void onServiceConnected() {
        if (server != null) return;
        new Thread(() -> {
            try (ServerSocket listener = new ServerSocket(32761, 1, InetAddress.getByName("127.0.0.1"))) {
                server = listener;
                while (server != null) {
                    try (Socket client = listener.accept()) {
                        client.setSoTimeout(1500);
                        handle(client);
                    } catch (Exception e) { Log.e("DroidForkObserver", "request", e); }
                }
            } catch (IOException e) { Log.e("DroidForkObserver", "listen", e); }
        }, "droidfork-observer").start();
    }

    @Override public void onDestroy() {
        ServerSocket old = server;
        server = null;
        try { if (old != null) old.close(); } catch (IOException ignored) {}
        super.onDestroy();
    }
    private final Object focusSignal = new Object();
    private long focusAt;
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event.getEventType() == AccessibilityEvent.TYPE_VIEW_FOCUSED &&
                CONTACTS.contentEquals(event.getPackageName())) {
            synchronized (focusSignal) {
                focusAt = event.getEventTime();
                focusSignal.notifyAll();
            }
        }
    }
    @Override public void onInterrupt() {}

    private static final class Trace {
        private final long start = SystemClock.elapsedRealtimeNanos();
        private final StringBuilder events = new StringBuilder();
        synchronized void mark(String name) {
            if (events.length() != 0) events.append(',');
            events.append('"').append(name).append("\":")
                  .append((SystemClock.elapsedRealtimeNanos() - start) / 1000);
        }
        synchronized byte[] finish() {
            return ("{" + events + "}").getBytes(java.nio.charset.StandardCharsets.UTF_8);
        }
    }

    private void handle(Socket client) throws Exception {
        Trace trace = new Trace();
        DataInputStream in = new DataInputStream(client.getInputStream());
        DataOutputStream out = new DataOutputStream(client.getOutputStream());
        byte[] auth = new byte[32];
        in.readFully(auth);
        byte[] expected = new byte[32];
        try (DataInputStream key = new DataInputStream(getResources().openRawResource(
                getResources().getIdentifier("observer_key", "raw", getPackageName())))) { key.readFully(expected); }
        if (!MessageDigest.isEqual(auth, expected)) throw new IOException("invalid relay key");
        int x = in.readInt(), y = in.readInt();
        if (x == -1 && y == 0) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            AccessibilityNodeInfo focus = root == null ? null : root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (focus == null || !CONTACTS.contentEquals(focus.getPackageName()) ||
                !"Company".equals(text(focus.getHintText()))) throw new IOException("unexpected source draft");
            // Exercise read-only observation in the saved parent. Discard the
            // serialization and clear the service cache before taking the checkpoint.
            ByteArrayOutputStream discard = new ByteArrayOutputStream();
            XmlSerializer writer = Xml.newSerializer();
            writer.setOutput(discard,"UTF-8"); node(writer,root,0); writer.flush();
            if (android.os.Build.VERSION.SDK_INT >= 33) clearCache();
            out.writeBytes("PRIMED"); out.flush();
            return;
        }
        boolean eventDriven = (x & (1 << 28)) != 0;
        boolean hostInput = eventDriven || (x & (1 << 29)) != 0;
        boolean hostFrame = hostInput || (x & (1 << 30)) != 0;
        x &= ~((1 << 30) | (1 << 29) | (1 << 28));
        if (x < 0 || x >= 480 || y < 0 || y >= 854) throw new IOException("invalid point");
        trace.mark("authenticated");
        Rect company = new Rect();
        if (!hostInput) {
            AccessibilityNodeInfo before = getRootInActiveWindow();
            if (before == null || !CONTACTS.contentEquals(before.getPackageName()))
                throw new IOException("Contacts is not active");
            AccessibilityNodeInfo oldFocus = before.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (oldFocus == null || !"Company".equals(text(oldFocus.getHintText())))
                throw new IOException("expected initial company focus");
            oldFocus.getBoundsInScreen(company);
            trace.mark("initial_focus");
            Path point = new Path();
            point.moveTo(x, y);
            GestureDescription gesture = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(point, 0, 16)).build();
            CompletableFuture<Boolean> tapped = new CompletableFuture<>();
            main.post(() -> {
                trace.mark("gesture_dispatch");
                if (!dispatchGesture(gesture, new GestureResultCallback() {
                    @Override public void onCompleted(GestureDescription g) { tapped.complete(true); }
                    @Override public void onCancelled(GestureDescription g) { tapped.complete(false); }
                }, main)) tapped.complete(false);
            });
            if (!tapped.get(3, TimeUnit.SECONDS)) throw new IOException("input cancelled");
            trace.mark("gesture_complete");
        }
        if (eventDriven) {
            synchronized (focusSignal) {
                long limit = SystemClock.uptimeMillis() + 500;
                while (SystemClock.uptimeMillis() - focusAt > 750) {
                    long remaining = limit - SystemClock.uptimeMillis();
                    if (remaining <= 0) break;
                    focusSignal.wait(remaining);
                }
            }
            trace.mark("focus_event_wait_done");
        }
        if (ObserverBuild.SINGLE_TREE && hostFrame && android.os.Build.VERSION.SDK_INT >= 33) {
            // Check focus on the same fresh tree that is returned to the host.
            // Separate findFocus calls would make additional Contacts UI-thread trips.
            long until = SystemClock.uptimeMillis() + 2500;
            for (int attempt = 1; SystemClock.uptimeMillis() < until; attempt++) {
                clearCache();
                trace.mark("query_start_" + attempt);
                AccessibilityNodeInfo live = getRootInActiveWindow(
                        AccessibilityNodeInfo.FLAG_PREFETCH_DESCENDANTS_DEPTH_FIRST |
                        AccessibilityNodeInfo.FLAG_PREFETCH_UNINTERRUPTIBLE);
                trace.mark("root_ready_" + attempt);
                if (live != null && CONTACTS.contentEquals(live.getPackageName())) {
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                    XmlSerializer serializer = Xml.newSerializer();
                    serializer.setOutput(bytes, "UTF-8");
                    serializer.startTag(null, "hierarchy");
                    boolean phone = node(serializer, live, 0);
                    serializer.endTag(null, "hierarchy");
                    serializer.flush();
                    trace.mark("tree_read_" + attempt);
                    if (phone) {
                        out.writeInt(bytes.size()); bytes.writeTo(out);
                        byte[] stats = trace.finish();
                        out.writeInt(stats.length); out.write(stats); out.flush();
                        return;
                    }
                }
                SystemClock.sleep(8);
            }
            throw new IOException("Phone never focused in live hierarchy");
        }
        AccessibilityNodeInfo root = null;
        int polls = 0;
        long deadline = SystemClock.uptimeMillis() + 2000;
        while (SystemClock.uptimeMillis() < deadline) {
            polls++;
            trace.mark("query_start_"+polls);
            root = hostFrame && android.os.Build.VERSION.SDK_INT >= 33 ?
                getRootInActiveWindow(AccessibilityNodeInfo.FLAG_PREFETCH_DESCENDANTS_DEPTH_FIRST |
                                      AccessibilityNodeInfo.FLAG_PREFETCH_UNINTERRUPTIBLE) :
                getRootInActiveWindow();
            trace.mark("root_ready_"+polls);
            AccessibilityNodeInfo focus = root == null ? null : root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            trace.mark("focus_read_"+polls);
            if (focus != null && CONTACTS.contentEquals(focus.getPackageName()) && "Phone".equals(text(focus.getHintText()))) break;
            root = null; SystemClock.sleep(8);
        }
        if (root == null) throw new IOException("focus did not change");
        Rect phone = new Rect();
        root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT).getBoundsInScreen(phone);
        trace.mark("new_focus");
        CompletableFuture<Bitmap> frame = hostFrame ? null : screenshot(trace,0);
        ByteArrayOutputStream tree = new ByteArrayOutputStream();
        XmlSerializer xml = Xml.newSerializer();
        xml.setOutput(tree,"UTF-8"); xml.startTag(null,"hierarchy"); node(xml,root,0);
        xml.endTag(null,"hierarchy"); xml.flush();
        trace.mark("xml_ready");
        out.writeInt(tree.size()); tree.writeTo(out);
        if (hostFrame) {
            byte[] stats = trace.finish(); out.writeInt(stats.length); out.write(stats); out.flush();
            return;
        }
        Bitmap bitmap = frame.get(3,TimeUnit.SECONDS);
        // Focus can reach accessibility before the compositor displays the new underline.
        // Android rate-limits successive screenshots. Keep retries on the request clock.
        for (int retry = 0; activeUnderline(bitmap,company) || !activeUnderline(bitmap,phone); retry++) {
            bitmap.recycle();
            if (retry == 3) throw new IOException("compositor did not show the new focus");
            SystemClock.sleep(350);
            bitmap = screenshot(trace,retry+1).get(3,TimeUnit.SECONDS);
        }
        trace.mark("frame_verified");
        ByteArrayOutputStream png = new ByteArrayOutputStream();
        if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, png)) throw new IOException("PNG encoding");
        trace.mark("png_encoded");
        bitmap.recycle(); out.writeInt(png.size()); png.writeTo(out); out.flush();
        byte[] stats = trace.finish(); out.writeInt(stats.length); out.write(stats); out.flush();
    }

    private static boolean activeUnderline(Bitmap bitmap, Rect field) {
        for (int y=field.bottom-6; y<field.bottom; y++) {
            int pixel=bitmap.getPixel(field.centerX(),y);
            if (Color.blue(pixel)-Color.red(pixel)>48 && Color.blue(pixel)-Color.green(pixel)>32)
                return true;
        }
        return false;
    }

    private CompletableFuture<Bitmap> screenshot(Trace trace,int attempt) {
        CompletableFuture<Bitmap> frame = new CompletableFuture<>();
        trace.mark("capture_start_"+attempt);
        takeScreenshot(Display.DEFAULT_DISPLAY, getMainExecutor(), new TakeScreenshotCallback() {
            @Override public void onSuccess(ScreenshotResult r) {
                trace.mark("capture_callback_"+attempt);
                try (HardwareBuffer b = r.getHardwareBuffer()) {
                    Bitmap bitmap = Bitmap.wrapHardwareBuffer(b, r.getColorSpace());
                    frame.complete(bitmap.copy(Bitmap.Config.ARGB_8888, false));
                    bitmap.recycle();
                } catch (Exception e) { frame.completeExceptionally(e); }
            }
            @Override public void onFailure(int code) { frame.completeExceptionally(new IOException("frame " + code)); }
        });
        return frame;
    }

    private static String text(CharSequence s) { return s == null ? "" : s.toString(); }
    private static boolean node(XmlSerializer xml, AccessibilityNodeInfo n, int depth) throws IOException {
        xml.startTag(null,"node");
        Rect r = new Rect(); n.getBoundsInScreen(r);
        xml.attribute(null,"bounds","["+r.left+","+r.top+"]["+r.right+","+r.bottom+"]");
        xml.attribute(null,"class",text(n.getClassName()));
        xml.attribute(null,"package",text(n.getPackageName()));
        xml.attribute(null,"resource-id",text(n.getViewIdResourceName()));
        xml.attribute(null,"text",text(n.getText()));
        xml.attribute(null,"hint",text(n.getHintText()));
        xml.attribute(null,"content-desc",text(n.getContentDescription()));
        xml.attribute(null,"focused",Boolean.toString(n.isFocused()));
        xml.attribute(null,"enabled",Boolean.toString(n.isEnabled()));
        boolean phone = n.isFocused() && "Phone".equals(text(n.getHintText())) && CONTACTS.contentEquals(n.getPackageName());
        for (int i=0; depth<64 && i<n.getChildCount(); i++) {
            AccessibilityNodeInfo child = n.getChild(i);
            if (child != null) phone |= node(xml,child,depth+1);
        }
        xml.endTag(null,"node");
        return phone;
    }
}
