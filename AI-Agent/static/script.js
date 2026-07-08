// --- DOM GLOBAL MAPS ---
const input = document.getElementById("prompt");
const sendBtn = document.getElementById("send-btn"); 
const messages = document.getElementById("messages");
const welcomeSection = document.querySelector(".welcome");
const contentPane = document.getElementById("content-pane");
const chipsContainer = document.querySelector(".chips-container");
const historyBox = document.getElementById("sidebar-history-box");
const recentsToggle = document.getElementById("recents-toggle");
const searchInput = document.getElementById("sidebar-search");

// Sidebar Toggle Elements
const sidebar = document.querySelector(".sidebar");
const openSidebarBtn = document.getElementById("open-sidebar-btn");
const closeSidebarBtn = document.getElementById("close-sidebar-btn");

const addIcon = document.querySelector(".add-icon");
const hiddenFileInput = document.getElementById("hidden-file-input");
const dropzoneOverlay = document.getElementById("dropzone-overlay");

const settingsBtn = document.querySelector(".profile-settings-btn");
const settingsModal = document.getElementById("settings-modal");
const closeModalBtn = document.getElementById("close-modal-btn");
const clearCacheBtn = document.getElementById("clear-cache-btn");
const exportHistoryBtn = document.getElementById("export-history-btn");

// Custom Profile & Preferences DOM Elements
const userNicknameInput = document.getElementById("user-nickname");
const userOccupationInput = document.getElementById("user-occupation");
const userAboutInput = document.getElementById("user-about");
const customInstructionsInput = document.getElementById("custom-instructions");

// Artifact Panel Engine Interfaces
const artifactDrawer = document.getElementById("artifact-drawer");
const closeArtifactBtn = document.getElementById("close-artifact-btn");
const artifactIframe = document.getElementById("artifact-sandbox-iframe");
const artifactTitleText = document.getElementById("artifact-title-text");

// UPGRADE BUTTON MAPS
const manualCanvasBtn = document.getElementById("manual-canvas-btn");
const toggleMonospaceBtn = document.getElementById("toggle-monospace-btn");
const themeProfileSelector = document.getElementById("theme-profile-selector");

let chatHistory = JSON.parse(localStorage.getItem("chatHistory")) || [];
let activeChatId = null; 

// SEPARATED PARSED BUFFERS FOR SYSTEM STAGING
let stagedFilesList = [];  // { name: string, text: string }
let stagedImagesList = []; // { name: string, type: string, base64: string, url: string }

let generationIsActive = false;

marked.setOptions({ breaks: true, gfm: true });
const renderer = new marked.Renderer();

// PRODUCTION CODE INTERFACE PARSER
renderer.code = function(codePayload, infostring) {
    let actualCode = "";
    let lang = "text";

    if (codePayload && typeof codePayload === "object") {
        actualCode = codePayload.text || "";
        lang = codePayload.lang || infostring || "text";
    } else {
        actualCode = codePayload || "";
        lang = infostring || "text";
    }

    lang = lang.toLowerCase().replace(/[^a-z0-9]/g, "").trim();

    let safeCode = "";
    try {
        safeCode = btoa(unescape(encodeURIComponent(actualCode)));
    } catch (e) {
        console.error("Base64 Staging Generation Error:", e);
    }

    const previewable = ["html", "htm", "css", "svg", "xml"].includes(lang);

    return `
<div class="code-container">
    <div class="code-header">
        <span>${lang.toUpperCase()}</span>
        <div style="display:flex; gap:10px; align-items:center;">
            ${
                previewable
                ? `
                <span
                class="material-symbols-outlined open-artifact-btn"
                style="cursor: pointer; color: var(--accent-color);"
                onclick="window.triggerArtifactRuntimeRender('${safeCode}','${lang}')">
                open_in_new
                </span>
                `
                : ""
            }
            <span
            class="material-symbols-outlined copy-code-btn"
            style="cursor: pointer;"
            onclick="window.executeSystemClipboardCopy(this,'${safeCode}')">
            content_copy
            </span>
        </div>
    </div>
<pre><code class="language-${lang}">${actualCode.replace(/</g,"&lt;").replace(/>/g,"&gt;")}</code></pre>
</div>
`;
};

marked.use({ renderer });
initializeConsoleAccentTheme();
initializeThemeProfileEngine();


// --- MOBILE OVERLAY & GESTURE SYSTEM INTEGRATION ---

// Dynamically generate the dimming panel layer if not in DOM array
let mobileOverlay = document.querySelector(".mobile-sidebar-overlay");
if (!mobileOverlay) {
    mobileOverlay = document.createElement("div");
    mobileOverlay.className = "mobile-sidebar-overlay";
    document.body.appendChild(mobileOverlay);
}

// Ensure layout initial states match mobile profiles cleanly on load rules
if (window.innerWidth <= 768) {
    sidebar.classList.add("collapsed");
}

function openMobileSidebar() {
    sidebar.classList.remove("collapsed");
    mobileOverlay.classList.add("active");
    if (openSidebarBtn) openSidebarBtn.style.display = "none";
}

function closeMobileSidebar() {
    sidebar.classList.add("collapsed");
    mobileOverlay.classList.remove("active");
    if (openSidebarBtn) openSidebarBtn.style.display = "block";
}

if (closeSidebarBtn) {
    closeSidebarBtn.addEventListener("click", closeMobileSidebar);
}
if (openSidebarBtn) {
    openSidebarBtn.addEventListener("click", openMobileSidebar);
}
mobileOverlay.addEventListener("click", closeMobileSidebar);

// High-Fidelity Multi-Swipe Tracking Interfaces
let touchStartX = 0;
let touchStartY = 0;
let touchEndX = 0;
let touchEndY = 0;

window.addEventListener("touchstart", (e) => {
    touchStartX = e.changedTouches[0].clientX;
    touchStartY = e.changedTouches[0].clientY;
}, { passive: true });

window.addEventListener("touchend", (e) => {
    touchEndX = e.changedTouches[0].clientX;
    touchEndY = e.changedTouches[0].clientY;
    handleSwipeGestures();
}, { passive: true });

function handleSwipeGestures() {
    const swipeDistanceX = touchEndX - touchStartX;
    const swipeDistanceY = touchEndY - touchStartY;
    
    // Verify movement is clean horizontal shift rather than a vertical scrolling track
    if (Math.abs(swipeDistanceX) > Math.abs(swipeDistanceY)) {
        const minSwipeThreshold = 50; // trigger displacement line filter
        
        if (sidebar.classList.contains("collapsed")) {
            // OPEN SIDEBAR: Swipe Left-to-Right starting near the left boundary edge
            if (swipeDistanceX > minSwipeThreshold && touchStartX < 60) {
                openMobileSidebar();
            }
        } else {
            // CLOSE SIDEBAR: Swipe Right-to-Left originating from anywhere within view
            if (swipeDistanceX < -minSwipeThreshold) {
                closeMobileSidebar();
            }
        }
    }
}


// --- FEATURE 1: MANUAL SEND TO CANVAS CONTROLLER ---
manualCanvasBtn.addEventListener("click", () => {
    const textContent = input.value.trim();
    if (!textContent) {
        alert("Please paste your HTML, CSS, or SVG code into the chat area input field first!");
        return;
    }

    let targetSource = textContent;
    let inferredLang = "html";

    if (textContent.startsWith("```")) {
        const structuralLines = textContent.split("\n");
        const headerInfo = structuralLines[0].toLowerCase().replace("```", "").trim();
        if (headerInfo) inferredLang = headerInfo;
        
        structuralLines.shift(); 
        if (structuralLines[structuralLines.length - 1].trim() === "```") {
            structuralLines.pop(); 
        }
        targetSource = structuralLines.join("\n");
    } else if (textContent.includes("<html") || textContent.includes("<!DOCTYPE") || textContent.includes("<div")) {
        inferredLang = "html";
    } else if (textContent.includes("{") && textContent.includes(":")) {
        inferredLang = "css";
    }

    try {
        const generatedSafeToken = btoa(unescape(encodeURIComponent(targetSource)));
        window.triggerArtifactRuntimeRender(generatedSafeToken, inferredLang);
    } catch (err) {
        console.error("Manual Staging Sandbox Render Error:", err);
    }
});

// --- FEATURE 2: LIVE THEME SELECTOR HANDLER ---
function initializeThemeProfileEngine() {
    const currentlySavedTheme = localStorage.getItem("workstationThemeProfile") || "cyber-neon";
    themeProfileSelector.value = currentlySavedTheme;
    applyThemeProfileStyles(currentlySavedTheme);

    themeProfileSelector.addEventListener("change", (e) => {
        const targetTheme = e.target.value;
        localStorage.setItem("workstationThemeProfile", targetTheme);
        applyThemeProfileStyles(targetTheme);
    });
}

function applyThemeProfileStyles(theme) {
    const rootElement = document.documentElement;
    if (theme === "frosted-arctic") {
        rootElement.style.setProperty('--glass-bg', 'rgba(255, 255, 255, 0.08)');
        rootElement.style.setProperty('--glass-bg-hover', 'rgba(255, 255, 255, 0.15)');
        rootElement.style.setProperty('--glass-border', 'rgba(255, 255, 255, 0.25)');
        rootElement.style.setProperty('--glass-blur', 'blur(24px)');
        rootElement.style.setProperty('--glass-shadow', '0 8px 32px 0 rgba(255, 255, 255, 0.03)');
        document.body.style.background = "#141619";
    } else if (theme === "obsidian-minimalist") {
        rootElement.style.setProperty('--glass-bg', 'rgba(10, 10, 10, 0.2)');
        rootElement.style.setProperty('--glass-bg-hover', 'rgba(20, 20, 20, 0.4)');
        rootElement.style.setProperty('--glass-border', 'rgba(255, 255, 255, 0.03)');
        rootElement.style.setProperty('--glass-blur', 'blur(8px)');
        rootElement.style.setProperty('--glass-shadow', 'none');
        document.body.style.background = "#020202";
    } else {
        rootElement.style.setProperty('--glass-bg', 'rgba(255, 255, 255, 0.03)');
        rootElement.style.setProperty('--glass-bg-hover', 'rgba(255, 255, 255, 0.07)');
        rootElement.style.setProperty('--glass-border', 'rgba(255, 255, 255, 0.08)');
        rootElement.style.setProperty('--glass-blur', 'blur(16px)');
        rootElement.style.setProperty('--glass-shadow', '0 8px 32px 0 rgba(0, 0, 0, 0.37)');
        document.body.style.background = "#060606";
    }
}

// --- FEATURE 3: MONOSPACE INPUT STYLE TOGGLER ---
toggleMonospaceBtn.addEventListener("click", () => {
    input.classList.toggle("monospace-editor-active");
    if (input.classList.contains("monospace-editor-active")) {
        toggleMonospaceBtn.style.color = "var(--accent-color)";
        input.setAttribute("placeholder", "Entering Monospace Editor Mode. Write clean scripts or paste parameters safely...");
    } else {
        toggleMonospaceBtn.style.color = "";
        input.setAttribute("placeholder", "Ask anything or paste code to preview...");
    }
    input.focus();
});

window.executeSystemClipboardCopy = function(element, base64Code) {
    const code = decodeURIComponent(escape(atob(base64Code)));
    navigator.clipboard.writeText(code).then(() => {
        element.textContent = "check";
        element.classList.add("copy-success-state");
        setTimeout(() => {
            element.textContent = "content_copy";
            element.classList.remove("copy-success-state");
        }, 1500);
    });
};

window.triggerArtifactRuntimeRender = function (base64Code, lang) {
    const code = decodeURIComponent(escape(atob(base64Code)));

    artifactDrawer.classList.add("open-active");
    artifactTitleText.textContent = `Live Canvas Preview (${lang.toUpperCase()})`;

    const wrapper = document.querySelector(".artifact-frame-body-wrapper");
    wrapper.innerHTML = `<iframe id="artifact-sandbox-iframe" sandbox="allow-scripts allow-same-origin" style="width: 100%; height: 100%; border: none; background: #ffffff;"></iframe>`;
    
    const dynamicIframe = document.getElementById("artifact-sandbox-iframe");
    const iframeDoc = dynamicIframe.contentDocument || dynamicIframe.contentWindow.document;
    iframeDoc.open();

    if (lang === "html" || lang === "htm") {
        const isFullDocument = /<\s*(!doctype|html|head)\b/i.test(code);
        if (isFullDocument) {
            iframeDoc.write(code);
        } else {
            iframeDoc.write(`
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
${code}
</body>
</html>
            `);
        }
    } else if (lang === "css") {
        iframeDoc.write(`
<!DOCTYPE html>
<html>
<head>
<style>
${code}
</style>
</head>
<body>
<div class="demo" style="padding:20px; font-family:sans-serif; color:#ccc;">
    CSS Preview Active. Inside Sandbox environment window rules apply.
</div>
</body>
</html>
        `);
    } else {
        iframeDoc.write(`
<pre style="padding:20px; font-family:monospace; white-space:pre-wrap; color: #333;">
${code}
</pre>
        `);
    }
    iframeDoc.close();
};

closeArtifactBtn.addEventListener("click", () => {
    artifactDrawer.classList.remove("open-active");
});

const attachmentsTray = document.createElement("div");
attachmentsTray.className = "attached-files-tray";
input.parentElement.parentElement.insertBefore(attachmentsTray, input.parentElement);

renderSidebarHistory();

input.addEventListener("input", function() {
    this.style.height = "auto";
    this.style.height = this.scrollHeight + "px";
    updateButtonVisualState();
});

function updateButtonVisualState() {
    if (generationIsActive) {
        sendBtn.textContent = "stop_circle";
        sendBtn.classList.add("stop-active-state");
    } else if (input.value.trim().length > 0 || stagedFilesList.length > 0 || stagedImagesList.length > 0) {
        sendBtn.textContent = "arrow_upward";
        sendBtn.classList.remove("stop-active-state");
    } else {
        sendBtn.textContent = "graphic_eq";
        sendBtn.classList.remove("stop-active-state");
    }
}

input.addEventListener("keydown", function(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleFormSubmissionTrigger(); }
});

sendBtn.addEventListener("click", handleFormSubmissionTrigger);

function handleFormSubmissionTrigger() {
    if (generationIsActive) {
        generationIsActive = false; updateButtonVisualState();
        const activeThinking = document.querySelector(".thinking-status-card");
        if (activeThinking) activeThinking.remove();
    } else {
        sendMessage();
    }
}

window.onbeforeunload = function() {
    if (generationIsActive) return "Generation is currently running. Are you sure you want to exit?";
};

window.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === 'n') { e.preventDefault(); startNewChat(); }
    if (e.ctrlKey && e.key.toLowerCase() === 's') { e.preventDefault(); openSettingsModalDashboard(); }
    if (e.key === "Escape") { settingsModal.style.display = "none"; }
});

function openSettingsModalDashboard() {
    settingsModal.style.display = "flex";
    updateGatewayQuotaMeters();

    userNicknameInput.value = localStorage.getItem("userNickname") || "";
    userOccupationInput.value = localStorage.getItem("userOccupation") || "";
    userAboutInput.value = localStorage.getItem("userAbout") || "";
    customInstructionsInput.value = localStorage.getItem("customInstructions") || "";
}

[userNicknameInput, userOccupationInput, userAboutInput, customInstructionsInput].forEach(element => {
    element.addEventListener("input", () => {
        localStorage.setItem("userNickname", userNicknameInput.value.trim());
        localStorage.setItem("userOccupation", userOccupationInput.value.trim());
        localStorage.setItem("userAbout", userAboutInput.value.trim());
        localStorage.setItem("customInstructions", customInstructionsInput.value.trim());
    });
});

recentsToggle.addEventListener("click", () => {
    recentsToggle.classList.toggle("collapsed-trigger");
    historyBox.classList.toggle("hidden-history");
});

document.querySelector(".new-chat").addEventListener("click", startNewChat);

window.seekActiveSandboxVideo = function(seconds) {
    let videoNode = document.getElementById("workspace-native-player");
    
    if (!videoNode) {
        const currentIframe = document.getElementById("artifact-sandbox-iframe");
        if (currentIframe) {
            const iframeDoc = currentIframe.contentDocument || currentIframe.contentWindow.document;
            videoNode = iframeDoc.querySelector("video");
        }
    }
    
    if (videoNode) {
        videoNode.currentTime = seconds;
        videoNode.play();
    }
};

function getActionRequestModalEls() {
    return {
        modal: document.getElementById("action-request-modal"),
        closeBtn: document.getElementById("close-action-request-btn"),
        allowBtn: document.getElementById("action-allow-btn"),
        denyBtn: document.getElementById("action-deny-btn"),
        typeTitle: document.getElementById("action-request-type-title"),
        desc: document.getElementById("action-request-description"),
        idSpan: document.getElementById("action-request-id"),
    };
}

window.__current_action_request__ = null;
window.__approved_action_ids__ = window.__approved_action_ids__ || [];

async function handleActionRequest(actionRequest) {
    const els = getActionRequestModalEls();
    if (!els.modal) {
        generateAIResponse("⚠️ Action requires permission, but action-request modal UI is missing.", performance.now());
        return;
    }

    window.__current_action_request__ = actionRequest;

    const reqId = actionRequest.request_id || "";
    const actionType = actionRequest.action_type || "action";
    const description = actionRequest.description || "Action requires your permission.";

    if (els.typeTitle) els.typeTitle.textContent = actionType;
    if (els.desc) els.desc.textContent = description;
    if (els.idSpan) els.idSpan.textContent = reqId;

    els.modal.style.display = "flex";

    if (els.allowBtn && !els.allowBtn.__bb_bound) {
        els.allowBtn.__bb_bound = true;
        els.allowBtn.addEventListener("click", async () => {
            try {
                await fetch("/agent/allow_action", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        request_id: reqId,
                        approved: true
                    })
                });
                if (reqId) {
                    if (!window.__approved_action_ids__.includes(reqId)) {
                        window.__approved_action_ids__.push(reqId);
                    }
                }
                els.modal.style.display = "none";
                // Resume agent execution
                await resumeAgentAfterPermission();
            } catch (e) {
                els.modal.style.display = "none";
                generateAIResponse(`❌ Permission approval failed: ${e.message || e}`, performance.now());
            }
        });
    }

    if (els.denyBtn && !els.denyBtn.__bb_bound) {
        els.denyBtn.__bb_bound = true;
        els.denyBtn.addEventListener("click", async () => {
            try {
                await fetch("/agent/allow_action", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        request_id: reqId,
                        approved: false
                    })
                });
            } finally {
                els.modal.style.display = "none";
                window.__current_action_request__ = null;
                generateAIResponse("Action denied. The agent will stop or choose an alternative.", performance.now());
            }
        });
    }

    if (els.closeBtn && !els.closeBtn.__bb_bound) {
        els.closeBtn.__bb_bound = true;
        els.closeBtn.addEventListener("click", () => {
            els.modal.style.display = "none";
        });
    }
}

async function resumeAgentAfterPermission() {
    try {
        if (window.__current_action_request__ && window.__current_action_request__.request_id) {
            const rid = window.__current_action_request__.request_id;
            if (!window.__approved_action_ids__.includes(rid)) window.__approved_action_ids__.push(rid);
        }
    } catch (e) {
        // ignore
    }

    // Re-call /agent/chat using the last user message from the active chat.
    const activeChat = chatHistory.find(c => c.id === activeChatId);
    if (!activeChat) return;
    const lastUserMsg = [...activeChat.messages].reverse().find(m => m.sender === 'user');
    const text = lastUserMsg ? (lastUserMsg.text || "") : input.value.trim();

    // In this UI, we don't preserve staged file context across the round-trip.
    // We send empty context to avoid accidentally writing wrong files.
    const activeHistoryPayload = activeChat.messages.slice(0);

    await fetch("/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            text: text,
            history: activeHistoryPayload,
            file_context: "",
            images: [],
            allow_state: true,
            approved_actions: window.__approved_action_ids__ || [],
            preferences: {
                nickname: localStorage.getItem("userNickname") || "",
                occupation: localStorage.getItem("userOccupation") || "",
                about: localStorage.getItem("userAbout") || "",
                instructions: localStorage.getItem("customInstructions") || ""
            }
        })
    }).then(async res => {
        const isJson = res.headers.get('content-type')?.includes('application/json');
        const data = isJson ? await res.json() : null;
        if (!res.ok) throw new Error(data?.error || `Server error ${res.status}`);
        return data;
    }).then(data => {
        if (data.success) {
            generateAIResponse(data.reply || "", performance.now());
        } else if (data.action_request) {
            handleActionRequest(data.action_request);
        } else {
            generateAIResponse(`⚠️ Agent error: ${data.error || 'unknown'}`, performance.now());
        }
    });
}

function sendMessage(){

    const text = input.value.trim();
    
    if(!text && stagedFilesList.length === 0 && stagedImagesList.length === 0) return;

    if (!activeChatId) { createNewChatSession(text || stagedImagesList[0]?.name || stagedFilesList[0]?.name); }

    let messageHtmlContent = "";
    let aggregatedFileContext = "";
    let trackingContainsVideo = false;

    if (stagedImagesList.length > 0) {
        stagedImagesList.forEach(img => {
            if (img.type.startsWith("video/")) {
                trackingContainsVideo = true;
                messageHtmlContent += `
                    <div class="msg-file-pill visual-vid-preview" style="position:relative; max-width:280px; padding:6px; background: rgba(0,0,0,0.2);">
                        <div class="micro-video-wrapper" style="width:40px; height:26px; border-radius:4px; overflow:hidden; background:#000;">
                            <video src="${img.url}" muted style="width:100%; height:100%; object-fit:cover;"></video>
                        </div>
                        <div style="font-size:12px; color:#fff; text-overflow:ellipsis; overflow:hidden; white-space:nowrap; flex:1; margin-left:8px;">${img.name} (MP4 Attached)</div>
                    </div>`;
            } else {
                messageHtmlContent += `
                    <div class="msg-file-pill visual-img-preview" style="position:relative; max-width:180px; padding:4px; overflow:hidden;">
                        <img src="data:${img.type};base64,${img.base64}" style="width:100%; border-radius:8px; display:block;" />
                        <div style="font-size:10px; color:#aaa; margin-top:4px; text-align:center; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${img.name}</div>
                    </div>`;
            }
        });
    }

    if (stagedFilesList.length > 0) {
        stagedFilesList.forEach(file => {
            messageHtmlContent += `<div class="msg-file-pill"><span class="material-symbols-outlined">description</span><span>${file.name}</span></div>`;
            aggregatedFileContext += `--- FILE: ${file.name} ---\n${file.text}\n\n`;
        });
    }
    
    if (text) messageHtmlContent += `<div class="msg-text-body" style="margin-top:6px;">${text}</div>`;

    renderUserBubble(messageHtmlContent);
    saveMessageToActiveChat("user", messageHtmlContent);
    
    const activeChatSession = chatHistory.find(c => c.id === activeChatId);
    const activeHistoryPayload = activeChatSession ? activeChatSession.messages.slice(0, -1) : [];
    const imagesPayloadSnapshot = [...stagedImagesList];

    if (trackingContainsVideo && imagesPayloadSnapshot[0]?.url) {
        artifactDrawer.classList.add("open-active");
        artifactTitleText.textContent = `Video Operational Player Matrix`;
        
        const wrapper = document.querySelector(".artifact-frame-body-wrapper");
        if (wrapper) {
            wrapper.innerHTML = `
                <div style="background:#090909; display:flex; justify-content:center; align-items:center; width:100%; height:100%; overflow:hidden;">
                    <video id="workspace-native-player" src="${imagesPayloadSnapshot[0].url}" controls style="max-width:95%; max-height:90vh; border-radius:12px; border: 1px solid rgba(255,255,255,0.15); box-shadow:0 20px 50px rgba(0,0,0,0.8);"></video>
                </div>
            `;
        }
    }

    input.value = ""; input.style.height = "auto";
    generationIsActive = true; updateButtonVisualState();
    
    stagedFilesList = []; stagedImagesList = []; attachmentsTray.innerHTML = ""; 
    messages.scrollTop = messages.scrollHeight;

    const startTimeMark = performance.now();
    
    const thinkingStatusElement = document.createElement("div");
    thinkingStatusElement.className = "thinking-status-card";
    
    let currentStatusIndex = 0;
    let statusSequence = ["Thinking..."];
    
    if (trackingContainsVideo) {
        statusSequence = ["Reading video container...", "Decoding MP4 frame matrices...", "Analyzing temporal contexts...", "Generating description..."];
    } else if (imagesPayloadSnapshot.length > 0) {
        statusSequence = ["Analyzing image...", "Thinking...", "Generating answer..."];
    } else {
        statusSequence = ["Thinking...", "Searching web sources...", "Generating answer..."];
    }
    
    thinkingStatusElement.innerHTML = `
        <div class="thinking-indicator">
            <span class="thinking-dot"></span>
        </div>
        <span class="thinking-status-text">${statusSequence[0]}</span>
    `;
    
    messages.appendChild(thinkingStatusElement);
    messages.scrollTop = messages.scrollHeight;

    let statusInterval = setInterval(() => {
        if (currentStatusIndex < statusSequence.length - 1) {
            currentStatusIndex++;
            const textNode = thinkingStatusElement.querySelector(".thinking-status-text");
            if (textNode) textNode.textContent = statusSequence[currentStatusIndex];
        }
    }, 1250);

    fetch("/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
            text: text,
            history: activeHistoryPayload,
            file_context: aggregatedFileContext,
            images: imagesPayloadSnapshot,
            allow_state: false,
            approved_actions: window.__approved_action_ids__ || [],
            preferences: {
                nickname: localStorage.getItem("userNickname") || "",
                occupation: localStorage.getItem("userOccupation") || "",
                about: localStorage.getItem("userAbout") || "",
                instructions: localStorage.getItem("customInstructions") || ""
            }
        })
    })


    .then(async res => { 
        const isJson = res.headers.get('content-type')?.includes('application/json');
        const data = isJson ? await res.json() : null;
        
        if (!res.ok) {
            const errText = data?.error || `Server Error Status Code ${res.status}`;
            throw new Error(errText);
        }
        return data;
    })
    .then(data => {
        if (statusInterval) clearInterval(statusInterval);
        if (!generationIsActive) return;
        thinkingStatusElement.remove();
        generationIsActive = false; updateButtonVisualState();
        if (data.success) {
            let traceTag = data.was_searched ? ` | 🌐 Grounded` : ``;

            let finalProcessedReply = data.reply;
            const timestampRegex = /\[(\d{2}):(\d{2})\]/g;
            finalProcessedReply = finalProcessedReply.replace(timestampRegex, (match, mins, secs) => {
                const totalSecs = parseInt(mins, 10) * 60 + parseInt(secs, 10);
                return `<span class="video-timestamp-pill" onclick="window.seekActiveSandboxVideo(${totalSecs})"><span class="material-symbols-outlined" style="font-size:12px;">play_arrow</span>${mins}:${secs}</span>`;
            });

            generateAIResponse(finalProcessedReply, startTimeMark, traceTag, data.was_searched, false);
        } else if (data.action_request) {
            handleActionRequest(data.action_request);
        } else {
            generateAIResponse(`⚠️ Core Processing Error: ${data.error}`, startTimeMark);
        }

    })
    .catch(err => {
        if (statusInterval) clearInterval(statusInterval);
        if (!generationIsActive) return;
        thinkingStatusElement.remove();
        generationIsActive = false; updateButtonVisualState();
        generateAIResponse(`❌ System Connectivity Failure: ${err.message}`, startTimeMark);
    });
}

function renderUserBubble(text) {
    const userBubble = document.createElement("div");
    userBubble.className = "user-msg-bubble"; userBubble.innerHTML = text;
    messages.appendChild(userBubble); messages.scrollTop = messages.scrollHeight; 
}

function generateAIResponse(reply, startTimeMark, trackingSuffix = "", wasSearched = false, isImageCreation = false) {
    const totalLatencySec = ((performance.now() - startTimeMark) / 1000).toFixed(2);
    const wordCount = reply.split(" ").length;
    let metricsString = `Words: ${wordCount} | Latency: ${totalLatencySec}s${trackingSuffix}`;

    saveMessageToActiveChat("ai", reply, metricsString);

    const aiContainer = document.createElement("div");
    aiContainer.className = "ai-msg-container";

    const markdownWrapper = document.createElement("div");
    markdownWrapper.className = "response-markdown-body";

    aiContainer.appendChild(markdownWrapper);
    messages.appendChild(aiContainer);

    function appendSourcesDrawerLayout() {
        const utilityWrapper = document.createElement("div");
        utilityWrapper.innerHTML = generateFeedbackActionBar(metricsString);
        aiContainer.appendChild(utilityWrapper.firstElementChild);
        messages.scrollTop = messages.scrollHeight;
    }

    const containsCode = reply.includes("```") || reply.includes("<!DOCTYPE") || reply.includes("<html") || reply.includes("<head") || reply.includes("<body") || reply.includes("<style") || reply.includes("<script");

    if (containsCode || reply.includes("video-timestamp-pill")) {
        markdownWrapper.innerHTML = reply.startsWith("<p>") ? reply : marked.parse(reply);
        Prism.highlightAllUnder(markdownWrapper);
        appendSourcesDrawerLayout();
        return;
    }

    let index = 0;
    const words = reply.split(" ");
    let compiledText = "";

    const streamer = setInterval(() => {
        if (index < words.length) {
            compiledText += (index === 0 ? "" : " ") + words[index];
            markdownWrapper.innerHTML = marked.parse(compiledText);
            index++;
            messages.scrollTop = messages.scrollHeight;
        } else {
            clearInterval(streamer);
            markdownWrapper.innerHTML = marked.parse(reply);
            Prism.highlightAllUnder(markdownWrapper);
            appendSourcesDrawerLayout();
        }
    }, 20);
}

function generateFeedbackActionBar(metrics) {
    return `
        <div class="feedback-container">
            <div class="feedback-actions"><span class="material-symbols-outlined copy-msg-btn" onclick="executeClipboardMessageCopy(this)">content_copy</span><span class="material-symbols-outlined">thumb_up</span><span class="material-symbols-outlined">thumb_down</span></div>
            <div class="metrics-tag">${metrics || ""}</div>
        </div>`;
}

window.executeClipboardMessageCopy = function(element) {
    const parentContainer = element.closest('.ai-msg-container');
    const targetText = parentContainer.querySelector('.response-markdown-body').innerText;
    navigator.clipboard.writeText(targetText).then(() => {
        element.textContent = "check";
        element.style.color = "var(--accent-color)";
        setTimeout(() => {
            element.textContent = "content_copy";
            element.style.color = "";
        }, 1500);
    });
};

function renderSidebarHistory() {
    historyBox.innerHTML = "";
    chatHistory.forEach((chat) => {
        const navItem = document.createElement("div"); navItem.className = "nav-item dynamic-history-item"; navItem.dataset.id = chat.id;
        if (chat.id === activeChatId) navItem.classList.add("active");
        navItem.innerHTML = `
            <span class="material-symbols-outlined">chat_bubble</span>
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">${chat.title}</span>
            <button class="delete-chat-btn"><span class="material-symbols-outlined">delete</span></button>`;
        navItem.addEventListener("click", (e) => { if (!e.target.closest(".delete-chat-btn")) openChatSession(chat.id); });
        navItem.querySelector(".delete-chat-btn").addEventListener("click", (e) => { e.stopPropagation(); deleteChatSession(chat.id); });
        historyBox.appendChild(navItem);
    });
}

function createNewChatSession(firstMessageText) {
    const truncatedTitle = firstMessageText.length > 22 ? firstMessageText.substring(0, 22) + "..." : firstMessageText;
    const newChat = { id: Date.now(), title: truncatedTitle, messages: [] };
    activeChatId = newChat.id; chatHistory.unshift(newChat); localStorage.setItem("chatHistory", JSON.stringify(chatHistory));
    welcomeSection.style.display = "none";
    if (document.querySelector(".chips-container")) document.querySelector(".chips-container").style.display = "none";
    messages.style.display = "flex"; contentPane.classList.add("chatting-mode"); renderSidebarHistory();
}

function saveMessageToActiveChat(sender, text, metrics) {
    const currentChat = chatHistory.find(chat => chat.id === activeChatId);
    if (currentChat) {
        currentChat.messages.push({ sender: sender, text: text, metrics: metrics || "" });
        localStorage.setItem("chatHistory", JSON.stringify(chatHistory));
    }
}

function openChatSession(id) {
    activeChatId = id; const targetChat = chatHistory.find(chat => chat.id === activeChatId);
    if (!targetChat) return;
    welcomeSection.style.display = "none";
    if (document.querySelector(".chips-container")) document.querySelector(".chips-container").style.display = "none";
    messages.style.display = "flex"; contentPane.classList.add("chatting-mode"); messages.innerHTML = "";
    
    targetChat.messages.forEach(msg => {
        const bubble = document.createElement("div");
        if (msg.sender === "user") {
            bubble.className = "user-msg-bubble"; bubble.innerHTML = msg.text;
        } else {
            bubble.className = "ai-msg-container";
            const markdownWrapper = document.createElement("div");
            markdownWrapper.className = "response-markdown-body";
            markdownWrapper.innerHTML = msg.text.startsWith("<span") || msg.text.includes("video-timestamp-pill") ? msg.text : marked.parse(msg.text);
            bubble.appendChild(markdownWrapper); bubble.innerHTML += generateFeedbackActionBar(msg.metrics);
            setTimeout(() => Prism.highlightAllUnder(markdownWrapper), 50);
        }
        messages.appendChild(bubble);
    });
    messages.scrollTop = messages.scrollHeight; renderSidebarHistory(); 
}

function deleteChatSession(id) {
    chatHistory = chatHistory.filter(chat => chat.id !== id); localStorage.setItem("chatHistory", JSON.stringify(chatHistory));
    if (activeChatId === id) startNewChat(); else renderSidebarHistory();
}

function startNewChat() {
    activeChatId = null; messages.innerHTML = ""; messages.style.display = "none";
    input.value = ""; input.style.height = "auto";
    if (welcomeSection) {
        welcomeSection.style.display = "block";
        if (document.querySelector(".chips-container")) document.querySelector(".chips-container").style.display = "flex";
        contentPane.classList.remove("chatting-mode");
    }
    artifactDrawer.classList.remove("open-active"); 
    renderSidebarHistory();
}

addIcon.addEventListener("click", () => { hiddenFileInput.click(); });
hiddenFileInput.addEventListener("change", (e) => { processIncomingFiles(e.target.files); hiddenFileInput.value = ""; });
window.addEventListener("dragenter", (e) => { e.preventDefault(); dropzoneOverlay.style.display = "flex"; });
dropzoneOverlay.addEventListener("dragleave", (e) => {
    e.preventDefault();
    if (e.clientX <= 0 || e.clientY <= 0 || e.clientX >= window.innerWidth || e.clientY >= window.innerHeight) dropzoneOverlay.style.display = "none";
});
window.addEventListener("dragover", (e) => { e.preventDefault(); });
window.addEventListener("drop", (e) => { e.preventDefault(); dropzoneOverlay.style.display = "none"; if (e.dataTransfer.files.length > 0) processIncomingFiles(e.dataTransfer.files); });

function processIncomingFiles(files) {
    for (let file of files) {
        const isImg = file.type.startsWith("image/");
        const isVid = file.type.startsWith("video/");
        const reader = new FileReader();
        
        reader.onload = function(evt) {
            const base64Data = evt.target.result.split(",")[1];
            if (isImg || isVid) {
                const videoUrl = isVid ? URL.createObjectURL(file) : null;
                const mediaObj = { name: file.name, type: file.type, base64: base64Data, url: videoUrl };
                if (!stagedImagesList.some(i => i.name === file.name)) {
                    stagedImagesList.push(mediaObj);
                    if (isVid) {
                        renderVideoChipInTray(mediaObj);
                    } else {
                        renderVisualChipInTray(mediaObj, evt.target.result);
                    }
                }
            } else {
                const fileObj = { name: file.name, text: evt.target.result };
                if (!stagedFilesList.some(f => f.name === file.name)) {
                    stagedFilesList.push(fileObj);
                    renderTextChipInTray(fileObj);
                }
            }
        };

        if (isImg || isVid) {
            reader.readAsDataURL(file);
        } else {
            reader.readAsText(file);
        }
    }
}

function renderVideoChipInTray(mediaObj) {
    const chip = document.createElement("div"); 
    chip.className = "file-preview-chip video-preview-chip";
    chip.style.borderColor = "var(--accent-color)";
    
    chip.innerHTML = `
        <div class="micro-video-wrapper" style="width:24px; height:18px; border-radius:3px; overflow:hidden; background:#000;">
            <video src="${mediaObj.url}" muted style="width:100%; height:100%; object-fit:cover;"></video>
        </div>
        <span class="file-name-text" style="margin-left:6px;">${mediaObj.name}</span>
        <span class="material-symbols-outlined remove-file-btn">close</span>`;
        
    chip.querySelector(".remove-file-btn").addEventListener("click", () => { 
        stagedImagesList = stagedImagesList.filter(i => i.name !== mediaObj.name); 
        chip.remove(); updateButtonVisualState(); 
    });
    
    const videoNode = chip.querySelector("video");
    chip.addEventListener("mouseenter", () => videoNode.play());
    chip.addEventListener("mouseleave", () => { videoNode.pause(); videoNode.currentTime = 0; });

    attachmentsTray.appendChild(chip); updateButtonVisualState();
}

function renderVisualChipInTray(mediaObj, dataUrl) {
    const chip = document.createElement("div"); 
    chip.className = "file-preview-chip";
    chip.style.borderColor = "var(--accent-color)";
    
    chip.innerHTML = `
        <img src="${dataUrl}" style="width:20px; height:20px; object-fit:cover; border-radius:4px;" />
        <span class="file-name-text">${mediaObj.name}</span>
        <span class="material-symbols-outlined remove-file-btn">close</span>`;
    chip.querySelector(".remove-file-btn").addEventListener("click", () => { 
        stagedImagesList = stagedImagesList.filter(i => i.name !== mediaObj.name); 
        chip.remove(); updateButtonVisualState(); 
    });
    attachmentsTray.appendChild(chip); updateButtonVisualState();
}

function renderTextChipInTray(fileObj) {
    const chip = document.createElement("div"); 
    chip.className = "file-preview-chip";
    chip.innerHTML = `<span class="material-symbols-outlined file-icon">description</span><span class="file-name-text">${fileObj.name}</span><span class="material-symbols-outlined remove-file-btn">close</span>`;
    chip.querySelector(".remove-file-btn").addEventListener("click", () => { 
        stagedFilesList = stagedFilesList.filter(f => f.name !== fileObj.name); 
        chip.remove(); updateButtonVisualState(); 
    });
    attachmentsTray.appendChild(chip); updateButtonVisualState();
}

searchInput.addEventListener("input", () => {
    const query = searchInput.value.toLowerCase().trim();
    document.querySelectorAll(".dynamic-history-item").forEach(item => {
        item.style.display = item.querySelector("span:not(.material-symbols-outlined)").textContent.toLowerCase().includes(query) ? "flex" : "none";
    });
});

settingsBtn.addEventListener("click", openSettingsModalDashboard);
closeModalBtn.addEventListener("click", () => { settingsModal.style.display = "none"; });

function initializeConsoleAccentTheme() { 
    const savedColor = localStorage.getItem("consoleAccentColor") || "#10b981";
    document.documentElement.style.setProperty('--accent-color', savedColor); 
    
    document.querySelectorAll(".color-dot").forEach(dot => {
        const dotColor = dot.getAttribute("data-color");
        if (dotColor === savedColor) dot.classList.add("selected-dot");
        
        dot.addEventListener("click", () => {
            document.querySelectorAll(".color-dot").forEach(d => d.classList.remove("selected-dot"));
            dot.classList.add("selected-dot");
            localStorage.setItem("consoleAccentColor", dotColor);
            document.documentElement.style.setProperty('--accent-color', dotColor);
        });
    });
}

function updateGatewayQuotaMeters() {
    const container = document.getElementById("quota-bars-container");
    if (!container) return;

    fetch("/quota_status")
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                container.innerHTML = ""; 
                
                for (let i = 1; i <= 5; i++) {
                    const status = data.tracker[i] || { used: 0, max: 1500 };
                    const percent = ((status.used / status.max) * 100).toFixed(0);
                    
                    const barRow = document.createElement("div");
                    barRow.className = "quota-bar-row";
                    barRow.style.cssText = "width: 100%; display: flex; flex-direction: column; gap: 4px;";
                    
                    barRow.innerHTML = `
                        <div style="display: flex; justify-content: space-between; font-size: 12px; color: #b4b4b4; font-family: monospace;">
                            <span>Key Slot #${i}</span>
                            <span>${status.used}/${status.max} (${percent}%)</span>
                        </div>
                        <div class="quota-meter-bg" style="width: 100%; height: 6px; background: #222; border-radius: 3px; overflow: hidden; position: relative;">
                            <div class="quota-meter-fill" style="width: ${percent}%; height: 100%; background: var(--accent-color); transition: width 0.4s ease; border-radius: 3px;"></div>
                        </div>
                    `;
                    container.appendChild(barRow);
                }
            }
        })
        .catch(err => console.error("Error retrieving cluster allocation matrices:", err));
}

window.toggleSourcesDrawerMatrix = function(element) {
    element.classList.toggle("expanded");
    const container = element.closest('.sources-collapse-wrapper');
    const body = container.querySelector('.sources-content-body');
    body.classList.toggle("show");
};

// --- FEATURE 4: GENERAL PRODUCTIVITY WORKFLOW ROUTERS ---
window.triggerWorkflow = function(type) {
    const promptInput = document.getElementById("prompt");
    const existingText = promptInput.value.trim();
    
    let commandInstruction = "";
    if (type === 'explain') {
        commandInstruction = "Break down the core concepts from the workspace files or query above into high-fidelity, easy-to-digest terms. Use clear logical analogies where applicable.";
    } else if (type === 'refactor') {
        commandInstruction = "Review the architecture of the provided system specifications or source snippets. Optimize patterns, reduce overhead, eliminate anomalies, and write beautifully structured code accompanied by explicit markdown inline-comments.";
    } else if (type === 'document') {
        commandInstruction = "Generate comprehensive, production-grade technical documentation detailing the components, APIs, layout elements, and configuration rules from the workspace context files above.";
    }

    if (existingText) {
        promptInput.value = existingText + "\n\n" + commandInstruction;
    } else {
        promptInput.value = commandInstruction;
    }
    
    promptInput.style.height = "auto";
    promptInput.style.height = promptInput.scrollHeight + "px";
    
    handleFormSubmissionTrigger();
};