package com.larrytitus.kindledashboard;

import com.amazon.kindle.kindlet.KindletContext;

import ixtab.jailbreak.Jailbreak;
import ixtab.jailbreak.SuicidalKindlet;

import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Container;
import java.awt.Dimension;
import java.awt.EventQueue;
import java.awt.Font;
import java.awt.Graphics;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Image;
import java.awt.Insets;
import java.awt.MediaTracker;
import java.awt.Toolkit;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.awt.event.KeyAdapter;
import java.awt.event.KeyEvent;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.AllPermission;
import java.util.Arrays;
import java.util.Properties;

import javax.swing.BorderFactory;
import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JTextArea;
import javax.swing.SwingConstants;

/** Native Kindle client for the server-rendered WMATA e-ink dashboard. */
public final class DashboardKindlet extends SuicidalKindlet {
    private static final String VERSION = "1.7";
    private static final String DEFAULT_URL =
            "http://192.168.0.10:8080/eink/home/kindle-pw2/";
    private static final String SETTINGS_DIR = "/mnt/us/extensions/wmata-dashboard";
    private static final String SETTINGS_FILE = SETTINGS_DIR + "/dashboard.properties";
    private KindletContext context;
    private Container root;
    private JPanel menuPanel;
    private DashboardCanvas dashboard;
    private JTextArea urlField;
    private JTextArea intervalField;
    private JTextArea fullRefreshField;
    private JTextArea statusLabel;
    private JButton retryButton;

    private volatile boolean dashboardVisible;
    private volatile boolean commandRunning;
    private volatile boolean retryRequested;
    private volatile int generation;
    private volatile int currentPage = 1;
    private volatile int pageCount = 1;
    private volatile int changedFrames;
    private byte[] lastFrame;
    private boolean started;

    protected Jailbreak instantiateJailbreak() {
        return new DashboardJailbreak();
    }

    protected void onCreate(KindletContext value) {
        super.onCreate(value);
        context = value;
        root = context.getRootContainer();
    }

    protected void onStart() {
        super.onStart();
        if (started) return;
        started = true;
        EventQueue.invokeLater(new Runnable() {
            public void run() {
                buildInterface();
                if (!jailbreak.isAvailable() || !jailbreak.isEnabled()) {
                    setStatus("The Kindle jailbreak bridge is unavailable. Re-run the Universal Hotfix, then restart the Kindle.");
                }
            }
        });
    }

    protected void onStop() {
        stopWorker();
        allowSleep();
        super.onStop();
    }

    protected void onDestroy() {
        stopWorker();
        allowSleep();
        super.onDestroy();
    }

    private void buildInterface() {
        dashboard = new DashboardCanvas();
        dashboard.addMouseListener(new MouseAdapter() {
            public void mousePressed(MouseEvent event) {
                handleDashboardTap(event.getX(), event.getY());
            }
        });
        dashboard.addKeyListener(new KeyAdapter() {
            public void keyPressed(KeyEvent event) {
                if (event.getKeyCode() == KeyEvent.VK_LEFT
                        || event.getKeyCode() == KeyEvent.VK_PAGE_UP) {
                    turnPage(-1);
                } else if (event.getKeyCode() == KeyEvent.VK_RIGHT
                        || event.getKeyCode() == KeyEvent.VK_PAGE_DOWN) {
                    turnPage(1);
                }
            }
        });

        menuPanel = new JPanel(new GridBagLayout());
        menuPanel.setBackground(Color.WHITE);
        menuPanel.setBorder(BorderFactory.createEmptyBorder(5, 10, 5, 10));
        GridBagConstraints c = new GridBagConstraints();
        c.gridx = 0;
        c.gridy = 0;
        c.weightx = 1.0;
        c.fill = GridBagConstraints.HORIZONTAL;
        c.anchor = GridBagConstraints.NORTH;
        c.insets = new Insets(3, 1, 3, 1);

        JLabel title = new JLabel("WMATA E-Ink Dashboard", SwingConstants.CENTER);
        title.setFont(new Font("SansSerif", Font.BOLD, 11));
        menuPanel.add(title, c);

        c.gridy++;
        JTextArea hint = multiline(
                "Footer or right edge: next page.  Left edge: previous page.\n"
                + "Tap elsewhere on the dashboard to reopen this menu.", 2);
        hint.setFont(new Font("SansSerif", Font.PLAIN, 7));
        menuPanel.add(hint, c);

        c.gridy++;
        menuPanel.add(button("Close Menu & Resume Dashboard", new ActionListener() {
            public void actionPerformed(ActionEvent event) { configureAndRun(true); }
        }), c);

        c.gridy++;
        menuPanel.add(fieldLabel("Dashboard URL"), c);
        c.gridy++;
        urlField = field();
        menuPanel.add(urlField, c);

        c.gridy++;
        menuPanel.add(fieldLabel("Refresh interval in seconds"), c);
        c.gridy++;
        intervalField = field();
        menuPanel.add(intervalField, c);

        c.gridy++;
        menuPanel.add(fieldLabel("Full refresh every N changes"), c);
        c.gridy++;
        fullRefreshField = field();
        menuPanel.add(fullRefreshField, c);

        c.gridy++;
        menuPanel.add(button("Save & Start Dashboard", new ActionListener() {
            public void actionPerformed(ActionEvent event) { configureAndRun(true); }
        }), c);

        c.gridy++;
        menuPanel.add(button("Show One Frame", new ActionListener() {
            public void actionPerformed(ActionEvent event) { configureAndRun(false); }
        }), c);

        c.gridy++;
        retryButton = button("Retry Now", new ActionListener() {
            public void actionPerformed(ActionEvent event) {
                retryRequested = true;
                retryButton.setVisible(false);
                setStatus("Retrying dashboard now...");
            }
        });
        retryButton.setVisible(false);
        menuPanel.add(retryButton, c);

        c.gridy++;
        menuPanel.add(button("About", new ActionListener() {
            public void actionPerformed(ActionEvent event) { showAbout(); }
        }), c);

        c.gridy++;
        menuPanel.add(button("Stop Dashboard & Exit", new ActionListener() {
            public void actionPerformed(ActionEvent event) { stopAndExit(); }
        }), c);

        c.gridy++;
        c.weighty = 1.0;
        statusLabel = multiline("Ready.", 3);
        statusLabel.setFont(new Font("SansSerif", Font.PLAIN, 7));
        menuPanel.add(statusLabel, c);

        loadSettings();
        showMenu();
    }

    private JLabel fieldLabel(String text) {
        JLabel label = new JLabel(text);
        label.setFont(new Font("SansSerif", Font.BOLD, 7));
        return label;
    }

    private JTextArea field() {
        final JTextArea value = new JTextArea("", 1, 1);
        value.setEditable(true);
        value.setFocusable(true);
        value.setLineWrap(false);
        value.setWrapStyleWord(false);
        value.setOpaque(true);
        value.setFont(new Font("SansSerif", Font.PLAIN, 9));
        value.setForeground(Color.BLACK);
        value.setBackground(Color.WHITE);
        value.setCaretColor(Color.BLACK);
        value.setBorder(BorderFactory.createLineBorder(Color.GRAY));
        value.setMargin(new Insets(4, 4, 4, 4));
        value.setPreferredSize(new Dimension(270, 38));
        value.setMinimumSize(new Dimension(120, 38));
        value.addKeyListener(new KeyAdapter() {
            public void keyTyped(KeyEvent event) {
                if (event.getKeyChar() == '\n' || event.getKeyChar() == '\r') {
                    event.consume();
                }
            }
        });
        return value;
    }

    private JTextArea multiline(String text, int rows) {
        JTextArea value = new JTextArea(text, rows, 1);
        value.setEditable(false);
        value.setFocusable(false);
        value.setLineWrap(true);
        value.setWrapStyleWord(true);
        value.setOpaque(false);
        value.setForeground(Color.BLACK);
        return value;
    }

    private JButton button(String text, ActionListener listener) {
        JButton value = new JButton(text);
        value.setFont(new Font("SansSerif", Font.BOLD, 9));
        value.setPreferredSize(new Dimension(270, 44));
        value.setMinimumSize(new Dimension(120, 44));
        value.addActionListener(listener);
        return value;
    }

    private void showMenu() {
        dashboardVisible = false;
        root.removeAll();
        root.setLayout(new BorderLayout());
        root.add(menuPanel, BorderLayout.CENTER);
        root.validate();
        root.repaint();
    }

    private void showDashboard(final byte[] content, final Image image, final boolean forceFull) {
        try {
            EventQueue.invokeAndWait(new Runnable() {
                public void run() {
                    dashboard.setFrame(image);
                    root.removeAll();
                    root.setLayout(new BorderLayout());
                    root.add(dashboard, BorderLayout.CENTER);
                    root.validate();
                    root.repaint();
                    dashboard.requestFocus();
                    dashboardVisible = true;
                }
            });
            if (forceFull) {
                try { Thread.sleep(250); } catch (InterruptedException ignored) { }
                requestFullRefresh();
            }
            lastFrame = content;
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private void configureAndRun(final boolean continuous) {
        final String requestedUrl = urlField.getText().trim();
        final int interval = positiveNumber(intervalField.getText());
        final int fullEvery = positiveNumber(fullRefreshField.getText());
        if (!isValidUrl(requestedUrl)) {
            setStatus("Enter a complete http:// URL without spaces.");
            return;
        }
        if (interval < 1 || fullEvery < 1) {
            setStatus("Refresh values must be positive whole numbers.");
            return;
        }
        saveSettings(requestedUrl, interval, fullEvery);
        startWorker(requestedUrl, interval, fullEvery, continuous);
    }

    private void startWorker(final String requestedUrl, final int interval,
            final int fullEvery, final boolean continuous) {
        stopWorker();
        final int thisGeneration = ++generation;
        retryRequested = false;
        retryButton.setVisible(false);
        setStatus(continuous
                ? "Connecting to the dashboard server... This may take up to 20 seconds."
                : "Loading one frame... This may take up to 20 seconds.");

        Thread worker = new Thread(new Runnable() {
            public void run() {
                while (generation == thisGeneration) {
                    try {
                        keepAwake();
                        commandRunning = true;
                        FrameResult result = fetchFrame(requestedUrl, currentPage);
                        if (generation != thisGeneration) return;
                        pageCount = result.pageCount;
                        if (currentPage > pageCount) currentPage = 1;
                        boolean changed = lastFrame == null || !Arrays.equals(lastFrame, result.bytes);
                        if (changed) changedFrames++;
                        boolean forceFull = changed && (lastFrame == null
                                || changedFrames % fullEvery == 0);
                        showDashboard(result.bytes, result.image, forceFull);
                        commandRunning = false;
                        if (!continuous) return;
                    } catch (Throwable error) {
                        commandRunning = false;
                        showFailure(readableError(error), interval, continuous, thisGeneration);
                        if (!continuous) return;
                    }

                    retryRequested = false;
                    int quarterSeconds = interval * 4;
                    for (int waited = 0; waited < quarterSeconds; waited++) {
                        if (generation != thisGeneration || retryRequested) break;
                        try { Thread.sleep(250); } catch (InterruptedException ignored) { }
                    }
                }
            }
        }, "WMATADashboard");
        worker.setDaemon(true);
        worker.start();
    }

    private void stopWorker() {
        generation++;
        retryRequested = true;
        commandRunning = false;
    }

    private FrameResult fetchFrame(String requestedUrl, int requestedPage) throws Exception {
        String base = baseUrl(requestedUrl);
        int discoveredPages = 1;
        try {
            String manifest = new String(download(base + "manifest.json"), "UTF-8");
            discoveredPages = pageCountFromManifest(manifest);
        } catch (Exception ignored) {
            // Direct image URLs remain useful even if a manifest is unavailable.
        }
        int page = requestedPage;
        if (page < 1 || page > discoveredPages) page = 1;
        String imageUrl;
        if (page == 1 && isDirectPng(requestedUrl)) {
            imageUrl = requestedUrl;
        } else {
            imageUrl = base + (page == 1 ? "dashboard.png" : "dashboard-" + page + ".png");
        }
        byte[] bytes = download(imageUrl);
        Image image = Toolkit.getDefaultToolkit().createImage(bytes);
        MediaTracker tracker = new MediaTracker(dashboard);
        tracker.addImage(image, 1);
        tracker.waitForID(1);
        if (tracker.isErrorID(1) || image.getWidth(null) < 1 || image.getHeight(null) < 1) {
            throw new IOException("The server response was not a valid dashboard PNG.");
        }
        return new FrameResult(bytes, image, discoveredPages);
    }

    private byte[] download(String address) throws Exception {
        File temporary = new File("/tmp/wmata-dashboard-" + System.currentTimeMillis()
                + "-" + Math.abs(address.hashCode()));
        try {
            Process process = Runtime.getRuntime().exec(new String[] {
                    "/bin/sh", "-c", "wget -q -T 10 -O \"$1\" \"$2\"",
                    "wmata-download", temporary.getAbsolutePath(), address
            });
            int status = process.waitFor();
            if (status != 0) {
                String details = new String(readStream(process.getErrorStream()), "UTF-8").trim();
                if (details.length() == 0) details = "native wget exited with status " + status;
                throw new IOException("Download failed: " + details);
            }
            FileInputStream input = new FileInputStream(temporary);
            byte[] result = readStream(input);
            input.close();
            if (result.length == 0) throw new IOException("The server returned an empty response.");
            return result;
        } finally {
            temporary.delete();
        }
    }

    private byte[] readStream(InputStream input) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int read;
        while ((read = input.read(buffer)) != -1) output.write(buffer, 0, read);
        return output.toByteArray();
    }

    private void handleDashboardTap(int x, int y) {
        if (!dashboardVisible || commandRunning) return;
        int width = Math.max(1, dashboard.getWidth());
        int height = Math.max(1, dashboard.getHeight());
        if (x <= width / 20) {
            turnPage(-1);
        } else if (x >= width - width / 20 || y >= height - height / 12) {
            turnPage(1);
        } else {
            stopWorker();
            allowSleep();
            setStatus("Dashboard stopped.");
            showMenu();
        }
    }

    private void turnPage(final int direction) {
        if (!dashboardVisible || commandRunning) return;
        commandRunning = true;
        final String requestedUrl = urlField.getText().trim();
        new Thread(new Runnable() {
            public void run() {
                try {
                    int next = currentPage + direction;
                    if (next < 1) next = pageCount;
                    if (next > pageCount) next = 1;
                    FrameResult result = fetchFrame(requestedUrl, next);
                    pageCount = result.pageCount;
                    currentPage = Math.min(next, pageCount);
                    changedFrames++;
                    showDashboard(result.bytes, result.image, true);
                } catch (Throwable error) {
                    final String message = readableError(error);
                    EventQueue.invokeLater(new Runnable() {
                        public void run() {
                            setStatus("Could not change page: " + message);
                            showMenu();
                        }
                    });
                } finally {
                    commandRunning = false;
                }
            }
        }, "WMATAPageTurn").start();
    }

    private void showFailure(final String details, final int retrySeconds,
            final boolean continuous, final int thisGeneration) {
        EventQueue.invokeLater(new Runnable() {
            public void run() {
                if (generation != thisGeneration) return;
                showMenu();
                String message = "Could not download the dashboard image. " + details;
                if (continuous) {
                    message += " Automatically retrying in " + retrySeconds + " seconds.";
                    retryButton.setVisible(true);
                }
                setStatus(message);
            }
        });
    }

    private void setStatus(final String text) {
        if (!EventQueue.isDispatchThread()) {
            EventQueue.invokeLater(new Runnable() {
                public void run() { setStatus(text); }
            });
            return;
        }
        statusLabel.setText(text);
    }

    private void showAbout() {
        JOptionPane.showMessageDialog(root,
                "WMATA Dashboard for Kindle PW2\nVersion " + VERSION
                + "\n\nServer-rendered 758 x 1024 e-ink pages.",
                "About", JOptionPane.INFORMATION_MESSAGE);
    }

    private void stopAndExit() {
        stopWorker();
        allowSleep();
        try {
            Runtime.getRuntime().exec(new String[] {"/bin/sh", "-c",
                    "lipc-set-prop com.lab126.appmgrd stop app://com.lab126.booklet.kindlet"});
        } catch (IOException error) {
            setStatus("Dashboard stopped. Press Home to exit.");
        }
    }

    private void loadSettings() {
        Properties values = new Properties();
        File file = new File(SETTINGS_FILE);
        if (file.isFile()) {
            try {
                FileInputStream input = new FileInputStream(file);
                values.load(input);
                input.close();
            } catch (IOException ignored) { }
        }
        urlField.setText(values.getProperty("url", DEFAULT_URL));
        intervalField.setText(values.getProperty("interval", "60"));
        fullRefreshField.setText(values.getProperty("fullRefreshEvery", "10"));
        urlField.setCaretPosition(0);
        intervalField.setCaretPosition(0);
        fullRefreshField.setCaretPosition(0);
    }

    private void saveSettings(String url, int interval, int fullEvery) {
        try {
            File directory = new File(SETTINGS_DIR);
            if (!directory.isDirectory()) directory.mkdirs();
            Properties values = new Properties();
            values.setProperty("url", url);
            values.setProperty("interval", String.valueOf(interval));
            values.setProperty("fullRefreshEvery", String.valueOf(fullEvery));
            FileOutputStream output = new FileOutputStream(SETTINGS_FILE);
            values.store(output, "WMATA Dashboard for Kindle PW2");
            output.close();
        } catch (IOException error) {
            setStatus("Could not save settings: " + readableError(error));
        }
    }

    private void keepAwake() {
        runQuietly("lipc-set-prop com.lab126.powerd preventScreenSaver 1");
    }

    private void allowSleep() {
        runQuietly("lipc-set-prop com.lab126.powerd preventScreenSaver 0");
    }

    private void requestFullRefresh() {
        String[] candidates = {
            "/var/local/kmc/bin/fbink", "/mnt/us/libkh/bin/fbink",
            "/mnt/us/extensions/FBInk/bin/fbink"
        };
        for (int i = 0; i < candidates.length; i++) {
            if (new File(candidates[i]).isFile()) {
                runQuietly(candidates[i] + " -f -s");
                return;
            }
        }
        runQuietly("command -v fbink >/dev/null 2>&1 && fbink -f -s");
    }

    private void runQuietly(String command) {
        try {
            Runtime.getRuntime().exec(new String[] {"/bin/sh", "-c", command});
        } catch (IOException ignored) { }
    }

    static boolean isValidUrl(String value) {
        return value != null && value.startsWith("http://") && value.length() > 7
                && value.indexOf(' ') < 0 && value.indexOf('\n') < 0
                && value.indexOf('\r') < 0 && value.indexOf('\t') < 0;
    }

    static boolean isDirectPng(String value) {
        return value != null && value.toLowerCase().endsWith(".png");
    }

    static String baseUrl(String value) {
        String result = value.trim();
        int query = result.indexOf('?');
        if (query >= 0) result = result.substring(0, query);
        int slash = result.lastIndexOf('/');
        if (!result.endsWith("/")) result = result.substring(0, slash + 1);
        return result;
    }

    static int positiveNumber(String value) {
        try {
            int result = Integer.parseInt(value.trim());
            return result > 0 ? result : -1;
        } catch (Exception ignored) {
            return -1;
        }
    }

    static int pageCountFromManifest(String manifest) {
        String key = "\"page_count\"";
        int keyStart = manifest.indexOf(key);
        if (keyStart < 0) return 1;
        int colon = manifest.indexOf(':', keyStart + key.length());
        if (colon < 0) return 1;
        int start = colon + 1;
        while (start < manifest.length() && Character.isWhitespace(manifest.charAt(start))) start++;
        int end = start;
        while (end < manifest.length() && Character.isDigit(manifest.charAt(end))) end++;
        if (end == start) return 1;
        try {
            return Math.max(1, Integer.parseInt(manifest.substring(start, end)));
        } catch (NumberFormatException ignored) {
            return 1;
        }
    }

    private static String readableError(Throwable error) {
        String message = error.getMessage();
        return message == null || message.length() == 0
                ? error.getClass().getName() : message;
    }

    private static final class DashboardJailbreak extends Jailbreak {
        public boolean enable() {
            if (!super.enable()) return false;
            return getContext().requestPermission(new AllPermission());
        }
    }

    private static final class FrameResult {
        final byte[] bytes;
        final Image image;
        final int pageCount;

        FrameResult(byte[] value, Image frame, int pages) {
            bytes = value;
            image = frame;
            pageCount = pages;
        }
    }

    private static final class DashboardCanvas extends JComponent {
        private Image frame;

        void setFrame(Image value) {
            frame = value;
            repaint();
        }

        protected void paintComponent(Graphics graphics) {
            graphics.setColor(Color.WHITE);
            graphics.fillRect(0, 0, getWidth(), getHeight());
            if (frame != null) graphics.drawImage(frame, 0, 0, getWidth(), getHeight(), this);
        }
    }
}
