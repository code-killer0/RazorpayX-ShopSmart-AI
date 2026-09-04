document.addEventListener('DOMContentLoaded', () => {

    function renderMarkdown(text) {
        if (!text) return '';
        // Escape HTML to prevent XSS
        let html = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        // `inline code`
        html = html.replace(/`([^`]+)`/g, '<code style="background:rgba(139,92,246,0.12);color:#c4b5fd;padding:2px 6px;border-radius:4px;font-size:0.88em;">$1</code>');

        // **bold**
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // *italic* (but not inside bold)
        html = html.replace(/(?<!\*)\*([^*]+?)\*(?!\*)/g, '<em>$1</em>');

        // Convert lines starting with • or - into styled list items
        html = html.replace(/^[•\-]\s*(.+)$/gm, '<div style="display:flex;gap:8px;align-items:flex-start;margin:4px 0;"><span style="color:#8b5cf6;font-weight:700;flex-shrink:0;">•</span><span>$1</span></div>');

        // Line breaks
        html = html.replace(/\n/g, '<br>');

        return html;
    }

    // DOM Cache
    const messagesContainer = document.getElementById('messages-container');
    const inputForm = document.getElementById('input-form');
    const chatInput = document.getElementById('chat-input');
    const clearChatBtn = document.getElementById('clear-chat-btn');
    const threadList = document.getElementById('thread-list');
    const newChatBtn = document.getElementById('new-chat-btn');

    // State Variables
    let currentThreadId = '';
    let threads = [];

    // Initialize Sessions
    initThreads();

    // Event Listeners
    inputForm.addEventListener('submit', handleFormSubmit);
    clearChatBtn.addEventListener('click', clearCurrentChat);
    newChatBtn.addEventListener('click', createNewThread);

    // Delegate quick prompt clicks
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('quick-prompt-btn')) {
            const queryText = e.target.getAttribute('data-query');
            if (queryText) {
                chatInput.value = queryText;
                if (typeof inputForm.requestSubmit === 'function') {
                    inputForm.requestSubmit();
                } else {
                    inputForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                }
            }
        }
    });


    async function generateAndSetTitle(thread, userMessage) {
        if (!thread || !userMessage) return;
        try {
            const res = await fetch('/api/generate-title', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: userMessage })
            });
            if (res.ok) {
                const data = await res.json();
                if (data.title) {
                    thread.title = data.title;
                    localStorage.setItem('shopsmart_threads', JSON.stringify(threads));
                    renderThreadsList();
                }
            }
        } catch (err) {
            console.error('Failed to generate dynamic session title:', err);
        }
    }

    async function initThreads() {
        // Load saved titles from localStorage
        const savedTitles = {};
        try {
            const saved = localStorage.getItem('shopsmart_threads');
            if (saved) {
                const parsed = JSON.parse(saved);
                parsed.forEach(t => {
                    if (t.id && t.title) savedTitles[t.id] = t.title;
                });
            }
        } catch (e) { }

        try {
            const response = await fetch('/api/threads');
            const data = await response.json();
            const dbThreads = data.threads || [];

            threads = dbThreads.map((tid, idx) => ({
                id: tid,
                title: savedTitles[tid] || `New Chat`,
                messages: []
            }));

            // If no threads exist in DB, create a default one
            if (threads.length === 0) {
                const newId = 'thread_' + Math.random().toString(36).substr(2, 9);
                threads.push({
                    id: newId,
                    title: 'New Chat',
                    messages: []
                });
            }

            currentThreadId = threads[0].id;
            renderThreadsList();
            await loadThreadMessages(currentThreadId);
        } catch (error) {
            console.error('Failed to load threads from server:', error);
            const saved = localStorage.getItem('shopsmart_threads');
            if (saved) {
                threads = JSON.parse(saved);
            }
            if (threads.length === 0) {
                threads.push({
                    id: 'thread_' + Math.random().toString(36).substr(2, 9),
                    title: 'New Chat',
                    messages: []
                });
            }
            currentThreadId = threads[0].id;
            renderThreadsList();
            loadThreadMessages(currentThreadId);
        }
    }

    function createNewThread() {
        const newId = 'thread_' + Math.random().toString(36).substr(2, 9);
        const newThread = {
            id: newId,
            title: 'New Chat',
            messages: []
        };
        threads.unshift(newThread);
        localStorage.setItem('shopsmart_threads', JSON.stringify(threads));
        currentThreadId = newId;

        renderThreadsList();
        loadThreadMessages(newId);
    }

    function renderThreadsList() {
        threadList.innerHTML = '';
        threads.forEach(thread => {
            const li = document.createElement('li');
            li.className = `thread-item ${thread.id === currentThreadId ? 'active' : ''}`;
            li.innerHTML = `
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                </svg>
                <span>${thread.title}</span>
                <button class="thread-delete-btn" title="Delete conversation" data-thread-id="${thread.id}">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                    </svg>
                </button>
            `;
            // Click on thread to load it
            li.addEventListener('click', async (e) => {
                if (e.target.closest('.thread-delete-btn')) return; // skip if delete btn clicked
                currentThreadId = thread.id;
                renderThreadsList();
                await loadThreadMessages(thread.id);
            });
            // Click delete button to remove thread from DB and UI
            const deleteBtn = li.querySelector('.thread-delete-btn');
            deleteBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await deleteThread(thread.id);
            });
            threadList.appendChild(li);
        });
    }

    async function deleteThread(threadId) {
        try {
            const res = await fetch(`/api/history/${threadId}`, { method: 'DELETE' });
            if (!res.ok) console.error('Failed to delete thread from server');
        } catch (error) {
            console.error('Failed to delete thread from server:', error);
        }
        threads = threads.filter(t => t.id !== threadId);
        localStorage.setItem('shopsmart_threads', JSON.stringify(threads));

        if (threads.length === 0) {
            const newId = 'thread_' + Math.random().toString(36).substr(2, 9);
            threads.push({ id: newId, title: 'New Chat', messages: [] });
        }
        if (threadId === currentThreadId) {
            currentThreadId = threads[0].id;
        }
        renderThreadsList();
        await loadThreadMessages(currentThreadId);
    }

    async function loadThreadMessages(threadId) {
        // Clear message box (except first welcome message)
        const welcome = messagesContainer.querySelector('.welcome');
        messagesContainer.innerHTML = '';
        if (welcome) {
            messagesContainer.appendChild(welcome);
        }

        try {
            const response = await fetch(`/api/history/${threadId}`);
            if (response.ok) {
                const data = await response.json();
                const history = data.messages || [];

                // Render history accurately with each message's own products
                history.forEach((msg) => {
                    appendMessageToUI(msg.role, msg.text, msg.products || []);
                });

                // Sync with local memory state
                const thread = threads.find(t => t.id === threadId);
                if (thread) {
                    thread.messages = history.map((msg) => ({
                        role: msg.role,
                        text: msg.text,
                        products: msg.products || []
                    }));

                    // Generate LLM topic title if thread currently has a generic name
                    if ((!thread.title || thread.title.startsWith('Session ') || thread.title === 'New Chat') && history.length > 0) {
                        const firstUser = history.find(m => m.role === 'user');
                        if (firstUser && firstUser.text) {
                            generateAndSetTitle(thread, firstUser.text);
                        }
                    }
                }
            }
        } catch (error) {
            console.error('Failed to load message history from server:', error);
        }
        scrollToBottom();
    }

    function saveMessageToThread(threadId, role, text, products = []) {
        const thread = threads.find(t => t.id === threadId);
        if (thread) {
            if (!thread.messages) thread.messages = [];
            thread.messages.push({ role, text, products });
            localStorage.setItem('shopsmart_threads', JSON.stringify(threads));
        }
    }

    async function clearCurrentChat() {
        const thread = threads.find(t => t.id === currentThreadId);
        if (thread) {
            try {
                await fetch(`/api/history/${currentThreadId}`, {
                    method: 'DELETE'
                });
            } catch (error) {
                console.error('Failed to delete history from server:', error);
            }
            threads = threads.filter(t => t.id !== currentThreadId);
            localStorage.setItem('shopsmart_threads', JSON.stringify(threads));

            if (threads.length === 0) {
                const newId = 'thread_' + Math.random().toString(36).substr(2, 9);
                threads.push({
                    id: newId,
                    title: 'New Chat',
                    messages: []
                });
            }
            currentThreadId = threads[0].id;
            renderThreadsList();
            loadThreadMessages(currentThreadId);
        }
    }

    /**
     * UI Renderer Helpers
     */
    function appendMessageToUI(role, text, products = []) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}`;

        const avatar = document.createElement('div');
        avatar.className = 'avatar';
        avatar.innerText = role === 'user' ? 'U' : 'AI';
        messageDiv.appendChild(avatar);

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        // Render text
        const p = document.createElement('p');
        p.innerHTML = renderMarkdown(text);
        contentDiv.appendChild(p);

        // Render product recommendations if present
        if (products && products.length > 0) {
            const tilesContainer = renderProductTilesContainer(products);
            if (tilesContainer) {
                contentDiv.appendChild(tilesContainer);
            }
        }

        messageDiv.appendChild(contentDiv);
        messagesContainer.appendChild(messageDiv);
        scrollToBottom();
    }

    /**
     * Show Product Details Modal with full product information
     */
    function showProductDetailsModal(prod) {
        const title = prod.title || 'Product Details';
        const brand = prod.brand || 'General';
        let price = prod.final_price || prod.initial_price || prod.price || 'N/A';
        if (typeof price === 'string') {
            price = price.replace(/"/g, '').replace(/'/g, '').trim();
        }
        const parsedPrice = parseFloat(price) || 0;
        const inrPrice = parsedPrice < 1000 ? (parsedPrice * 83.0).toFixed(2) : parsedPrice.toFixed(2);
        const rating = prod.rating || 'N/A';
        const imgUrl = prod.image_url || prod.image || '';
        const merchantId = prod.merchant_id || prod.account_number || prod.merchant || '';
        const description = prod.description || prod.desc || 'High quality catalog item with verified seller authenticity.';
        const categories = prod.categories || prod.category || 'General Catalog';
        const availability = prod.availability || prod.stock || 'In Stock';

        const modal = document.createElement('div');
        modal.className = 'product-details-overlay';
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(2, 4, 27, 0.85);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
            animation: fadeIn 0.25s ease forwards;
            padding: 20px;
        `;

        modal.innerHTML = `
            <div class="product-details-card" style="
                background: #0b1426;
                border: 1px solid rgba(56, 189, 248, 0.3);
                border-radius: 20px;
                width: 100%;
                max-width: 650px;
                max-height: 90vh;
                box-shadow: 0 25px 60px rgba(0, 0, 0, 0.85), 0 0 30px rgba(56, 189, 248, 0.15);
                overflow-y: auto;
                font-family: 'Inter', sans-serif;
                color: #ffffff;
                position: relative;
            ">
                <!-- Header -->
                <div style="
                    background: linear-gradient(135deg, #02042b 0%, #0c2340 100%);
                    padding: 18px 24px;
                    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    position: sticky;
                    top: 0;
                    z-index: 2;
                ">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="
                            background: rgba(56, 189, 248, 0.12);
                            color: #38bdf8;
                            font-size: 11px;
                            font-weight: 700;
                            padding: 4px 10px;
                            border-radius: 6px;
                            border: 1px solid rgba(56, 189, 248, 0.3);
                            letter-spacing: 0.05em;
                            text-transform: uppercase;
                        ">Product Specifications</span>
                    </div>
                    <button class="close-details-btn" style="
                        background: rgba(255, 255, 255, 0.06);
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        color: #94a3b8;
                        width: 32px;
                        height: 32px;
                        border-radius: 50%;
                        cursor: pointer;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-size: 16px;
                        transition: all 0.2s;
                    ">✕</button>
                </div>

                <!-- Body -->
                <div style="padding: 24px; display: flex; flex-direction: column; gap: 20px;">
                    <!-- Upper Row: Image & Info -->
                    <div style="display: flex; gap: 20px; flex-wrap: wrap;">
                        <div style="
                            width: 180px;
                            height: 180px;
                            background: #060c1e;
                            border: 1px solid rgba(255, 255, 255, 0.1);
                            border-radius: 14px;
                            overflow: hidden;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            flex-shrink: 0;
                        ">
                            ${imgUrl && imgUrl.startsWith('http') ? `<img src="${imgUrl}" alt="${title}" style="width:100%;height:100%;object-fit:cover;" onerror="this.onerror=null;this.parentElement.innerHTML=getFallbackImgSVG();">` : getFallbackImgSVG()}
                        </div>
                        <div style="flex: 1; min-width: 220px; display: flex; flex-direction: column; justify-content: space-between;">
                            <div>
                                <span style="
                                    font-size: 11px;
                                    font-weight: 700;
                                    text-transform: uppercase;
                                    letter-spacing: 0.08em;
                                    color: #7dd3fc;
                                    background: rgba(56, 189, 248, 0.1);
                                    border: 1px solid rgba(56, 189, 248, 0.25);
                                    padding: 2px 8px;
                                    border-radius: 6px;
                                ">${brand}</span>
                                <h2 style="
                                    font-family: 'Outfit', sans-serif;
                                    font-size: 1.25rem;
                                    font-weight: 700;
                                    color: #ffffff;
                                    margin-top: 8px;
                                    line-height: 1.3;
                                ">${title}</h2>
                            </div>
                            <div style="margin-top: 14px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;">
                                <div>
                                    <div style="font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 600;">Price</div>
                                    <div style="font-family: 'Outfit', sans-serif; font-size: 1.4rem; font-weight: 800; color: #34d399;">
                                        $${price} <span style="font-size: 0.85rem; color: #94a3b8; font-weight: 500;">(₹${inrPrice})</span>
                                    </div>
                                </div>
                                <div style="
                                    display: flex;
                                    align-items: center;
                                    gap: 6px;
                                    background: rgba(251, 191, 36, 0.1);
                                    border: 1px solid rgba(251, 191, 36, 0.25);
                                    color: #fbbf24;
                                    padding: 6px 12px;
                                    border-radius: 8px;
                                    font-weight: 700;
                                    font-size: 0.9rem;
                                ">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="#fbbf24"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
                                    <span>${rating} Rating</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Product Attributes Grid -->
                    <div style="
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                        gap: 12px;
                        background: rgba(255, 255, 255, 0.03);
                        border: 1px solid rgba(255, 255, 255, 0.06);
                        padding: 14px;
                        border-radius: 12px;
                    ">
                        <div>
                            <div style="font-size: 10px; color: #64748b; font-weight: 700; text-transform: uppercase;">CATEGORY</div>
                            <div style="font-size: 13px; font-weight: 600; color: #e2e8f0; margin-top: 3px;">${categories}</div>
                        </div>
                        <div>
                            <div style="font-size: 10px; color: #64748b; font-weight: 700; text-transform: uppercase;">AVAILABILITY</div>
                            <div style="font-size: 13px; font-weight: 600; color: #34d399; margin-top: 3px;">✔ ${availability}</div>
                        </div>
                        <div>
                            <div style="font-size: 10px; color: #64748b; font-weight: 700; text-transform: uppercase;">MERCHANT VENDOR</div>
                            <div style="font-size: 13px; font-weight: 600; color: #38bdf8; margin-top: 3px;">ShopSmart Verified</div>
                        </div>
                    </div>

                    <!-- Description Block -->
                    <div style="background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(255, 255, 255, 0.05); padding: 16px; border-radius: 12px;">
                        <div style="font-size: 11px; color: #64748b; font-weight: 700; text-transform: uppercase; margin-bottom: 6px;">DESCRIPTION</div>
                        <div style="font-size: 13px; color: #cbd5e1; line-height: 1.6;">${description}</div>
                    </div>

                    <!-- Action Footer Buttons -->
                    <div style="display: flex; gap: 12px; align-items: center; justify-content: flex-end; margin-top: 6px;">
                        <button class="ask-ai-btn" style="
                            background: rgba(56, 189, 248, 0.08);
                            border: 1px solid rgba(56, 189, 248, 0.25);
                            color: #7dd3fc;
                            padding: 12px 18px;
                            border-radius: 10px;
                            font-family: 'Outfit', sans-serif;
                            font-weight: 600;
                            font-size: 14px;
                            cursor: pointer;
                            transition: all 0.2s;
                            display: flex;
                            align-items: center;
                            gap: 8px;
                        ">
                            <span>Ask AI Chat</span> 💬
                        </button>
                        <button class="pay-details-btn" style="
                            background: linear-gradient(135deg, #146eb4 0%, #0066FF 100%);
                            border: 1px solid #38bdf8;
                            color: #ffffff;
                            padding: 12px 22px;
                            border-radius: 10px;
                            font-family: 'Outfit', sans-serif;
                            font-weight: 700;
                            font-size: 15px;
                            cursor: pointer;
                            box-shadow: 0 4px 16px rgba(56, 189, 248, 0.3);
                            transition: all 0.2s;
                            display: flex;
                            align-items: center;
                            gap: 8px;
                        ">
                            <span>Pay via Razorpay</span> ⚡
                        </button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        modal.querySelector('.close-details-btn').addEventListener('click', () => modal.remove());
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.remove();
        });

        modal.querySelector('.pay-details-btn').addEventListener('click', () => {
            modal.remove();
            showPreCheckoutModal(title, price, merchantId);
        });

        modal.querySelector('.ask-ai-btn').addEventListener('click', () => {
            modal.remove();
            chatInput.value = `Tell me more details about ${title}`;
            if (typeof inputForm.requestSubmit === 'function') {
                inputForm.requestSubmit();
            } else {
                inputForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
            }
        });
    }

    /**
     * Helper to render product tiles container with Details & Pay via Razorpay buttons
     */
    function renderProductTilesContainer(products) {
        if (!products || products.length === 0) return null;

        const tilesContainer = document.createElement('div');
        tilesContainer.className = 'product-tiles-container';

        products.forEach((prod) => {
            const title = prod.title || 'Product';
            const brand = prod.brand || 'General';
            let price = prod.final_price || prod.initial_price || prod.price || 'N/A';
            if (typeof price === 'string') {
                price = price.replace(/"/g, '').replace(/'/g, '').trim();
            }
            const rating = prod.rating || 'N/A';
            const imgUrl = prod.image_url || prod.image;
            const merchantId = prod.merchant_id || prod.account_number || prod.merchant || '';

            const tile = document.createElement('div');
            tile.className = 'product-tile';

            const imgWrapper = document.createElement('div');
            imgWrapper.className = 'product-img-wrapper';

            if (imgUrl && imgUrl.startsWith('http')) {
                const img = document.createElement('img');
                img.src = imgUrl;
                img.alt = title;
                img.onerror = () => {
                    imgWrapper.innerHTML = getFallbackImgSVG();
                };
                imgWrapper.appendChild(img);
            } else {
                imgWrapper.innerHTML = getFallbackImgSVG();
            }
            tile.appendChild(imgWrapper);

            const details = document.createElement('div');
            details.className = 'product-details';
            details.innerHTML = `
                <div class="product-header-row">
                    <span class="product-brand">${brand}</span>
                    <h3 class="product-title" title="${title}">${title}</h3>
                </div>
                <div class="product-meta-row">
                    <div class="product-price-section">
                        <span class="price-label">Price</span>
                        <span class="product-price">$${price}</span>
                    </div>
                    <div class="product-rating">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                        </svg>
                        <span>${rating}</span>
                    </div>
                    <div class="product-actions">
                        <button class="details-btn">Details</button>
                        <button class="buy-btn"><span>Pay via Razorpay</span> ⚡</button>
                    </div>
                </div>
            `;

            details.querySelector('.details-btn').addEventListener('click', () => {
                showProductDetailsModal(prod);
            });

            details.querySelector('.buy-btn').addEventListener('click', () => {
                showPreCheckoutModal(title, price, merchantId);
            });

            tile.appendChild(details);
            tilesContainer.appendChild(tile);
        });

        return tilesContainer;
    }

    function getFallbackImgSVG() {
        return `
            <div class="img-placeholder">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                    <circle cx="8.5" cy="8.5" r="1.5"></circle>
                    <polyline points="21 15 16 10 5 21"></polyline>
                </svg>
                <span>No Image</span>
            </div>
        `;
    }

    function appendLoaderToUI() {
        const loaderDiv = document.createElement('div');
        loaderDiv.className = 'message assistant temp-loader';

        const avatar = document.createElement('div');
        avatar.className = 'avatar';
        avatar.innerText = 'AI';
        loaderDiv.appendChild(avatar);

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.innerHTML = `
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        `;
        loaderDiv.appendChild(contentDiv);
        messagesContainer.appendChild(loaderDiv);
        scrollToBottom();
        return loaderDiv;
    }

    function scrollToBottom() {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

   
    function ensureRazorpayLoaded() {
        return new Promise((resolve) => {
            if (window.Razorpay) {
                resolve(true);
                return;
            }
            const script = document.createElement('script');
            script.id = 'rzp-checkout-script';
            script.src = 'https://checkout.razorpay.com/v1/checkout.js';
            script.async = true;
            script.onload = () => resolve(true);
            script.onerror = () => resolve(false);
            document.body.appendChild(script);
        });
    }

    /**
     * Show Pre-Checkout Recommendation Modal using sub-millisecond Recommendation Engine + MongoDB
     */
    async function showPreCheckoutModal(title, price, merchantId = '') {
        const parsedPrice = parseFloat(price) || 0;
        const baseInrPrice = parsedPrice < 1000 ? parsedPrice * 83.0 : parsedPrice;

        const overlay = document.createElement('div');
        overlay.className = 'precheckout-overlay';
        overlay.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(2, 4, 27, 0.88);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 10000;
            animation: fadeIn 0.2s ease forwards;
            padding: 20px;
        `;

        overlay.innerHTML = `
            <div style="
                background: #0b1426;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 24px;
                width: 100%;
                max-width: 680px;
                max-height: 90vh;
                box-shadow: 0 25px 60px rgba(0, 0, 0, 0.9), 0 0 35px rgba(56, 189, 248, 0.2);
                overflow-y: auto;
                font-family: 'Inter', sans-serif;
                color: #ffffff;
                position: relative;
            ">
                <!-- Header -->
                <div style="
                    background: linear-gradient(135deg, #02042b 0%, #0c2340 100%);
                    padding: 20px 26px;
                    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    position: sticky;
                    top: 0;
                    z-index: 2;
                ">
                    <div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="
                                background: linear-gradient(135deg, #38bdf8 0%, #8b5cf6 100%);
                                color: #ffffff;
                                font-size: 11px;
                                font-weight: 800;
                                padding: 3px 10px;
                                border-radius: 6px;
                                text-transform: uppercase;
                                letter-spacing: 0.05em;
                            ">  Cart</span>
                            <span style="font-size: 12px; color: #94a3b8; font-weight: 500;">MongoDB Products Catalog</span>
                        </div>
                        <h2 style="font-family: 'Outfit', sans-serif; font-size: 1.35rem; font-weight: 800; margin-top: 6px; color: #ffffff;">
                            Frequently Bought Together
                        </h2>
                    </div>
                    <button class="close-modal-btn" style="
                        background: rgba(255, 255, 255, 0.06);
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        color: #94a3b8;
                        width: 34px;
                        height: 34px;
                        border-radius: 50%;
                        cursor: pointer;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-size: 16px;
                        transition: all 0.2s;
                    ">✕</button>
                </div>

                <!-- Content -->
                <div style="padding: 24px; display: flex; flex-direction: column; gap: 20px;">
                    
                    <!-- Selected Main Product -->
                    <div style="
                        background: rgba(12, 35, 64, 0.6);
                        border: 1px solid rgba(56, 189, 248, 0.3);
                        border-radius: 16px;
                        padding: 16px 20px;
                        display: flex;
                        align-items: center;
                        justify-content: space-between;
                        gap: 16px;
                    ">
                        <div>
                            <span style="font-size: 11px; color: #38bdf8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;">YOUR SELECTED ITEM</span>
                            <h3 style="font-family: 'Outfit', sans-serif; font-size: 1.1rem; font-weight: 700; color: #ffffff; margin-top: 4px;">${title}</h3>
                        </div>
                        <div style="text-align: right; flex-shrink: 0;">
                            <div style="font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase;">Base Price</div>
                            <div style="font-family: 'Outfit', sans-serif; font-size: 1.25rem; font-weight: 800; color: #34d399;">₹${baseInrPrice.toFixed(2)}</div>
                        </div>
                    </div>

                    <!-- Recommendation Section -->
                    <div>
                        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
                            <span style="font-size: 0.95rem; font-weight: 700; color: #e2e8f0;">Recommended Add-Ons for You:</span>
                            <span id="rec-latency-badge" style="font-size: 11px; color: #a7f3d0; background: rgba(16, 185, 129, 0.12); padding: 3px 9px; border-radius: 6px; border: 1px solid rgba(16, 185, 129, 0.3); font-weight: 600;">
                                 Fetching recommendations...
                            </span>
                        </div>

                        <div id="recommendations-list" style="display: flex; flex-direction: column; gap: 12px;">
                            <div style="text-align: center; padding: 20px; color: #94a3b8;">
                                Loading smart add-on recommendations...
                            </div>
                        </div>
                    </div>

                    <!-- Summary & Action Bar -->
                    <div style="
                        background: rgba(0, 0, 0, 0.4);
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 16px;
                        padding: 18px 20px;
                        display: flex;
                        flex-direction: column;
                        gap: 14px;
                    ">
                        <div style="display: flex; align-items: center; justify-content: space-between;">
                            <div>
                                <div style="font-size: 12px; color: #94a3b8; font-weight: 600;">TOTAL ORDER SUMMARY</div>
                                <div id="summary-item-count" style="font-size: 13px; color: #cbd5e1; margin-top: 2px;">1 item selected</div>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-size: 12px; color: #94a3b8; font-weight: 600;">TOTAL PAYABLE</div>
                                <div id="summary-total-price" style="font-family: 'Outfit', sans-serif; font-size: 1.45rem; font-weight: 800; color: #38bdf8;">
                                    ₹${baseInrPrice.toFixed(2)}
                                </div>
                            </div>
                        </div>

                        <div style="display: flex; gap: 12px; align-items: center;">
                            <button id="skip-pay-btn" style="
                                flex: 1;
                                background: rgba(255, 255, 255, 0.06);
                                border: 1px solid rgba(255, 255, 255, 0.15);
                                color: #cbd5e1;
                                padding: 13px 16px;
                                border-radius: 12px;
                                font-family: 'Outfit', sans-serif;
                                font-weight: 600;
                                font-size: 14px;
                                cursor: pointer;
                                transition: all 0.2s;
                            ">
                                Skip & Pay Base Product
                            </button>

                            <button id="proceed-combined-pay-btn" style="
                                flex: 1.4;
                                background: linear-gradient(135deg, #146eb4 0%, #0066FF 100%);
                                border: 1px solid #38bdf8;
                                color: #ffffff;
                                padding: 13px 20px;
                                border-radius: 12px;
                                font-family: 'Outfit', sans-serif;
                                font-weight: 700;
                                font-size: 15px;
                                cursor: pointer;
                                box-shadow: 0 4px 20px rgba(56, 189, 248, 0.35);
                                transition: all 0.2s;
                                display: flex;
                                align-items: center;
                                justify-content: center;
                                gap: 8px;
                            ">
                                <span>Proceed to Razorpay</span> 
                            </button>
                        </div>
                    </div>

                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        overlay.querySelector('.close-modal-btn').addEventListener('click', () => overlay.remove());

        let selectedAddons = new Map(); // prod_title -> price

        function updateSummary() {
            let currentTotal = baseInrPrice;
            selectedAddons.forEach((addonPrice) => {
                currentTotal += addonPrice;
            });
            const totalItems = 1 + selectedAddons.size;
            const countEl = document.getElementById('summary-item-count');
            const totalEl = document.getElementById('summary-total-price');
            if (countEl) countEl.innerText = `${totalItems} item${totalItems > 1 ? 's' : ''} selected`;
            if (totalEl) totalEl.innerText = `₹${currentTotal.toFixed(2)}`;
        }

        // Handle skip button
        overlay.querySelector('#skip-pay-btn').addEventListener('click', () => {
            overlay.remove();
            initiateCheckout(title, price, merchantId);
        });

        // Handle proceed with recommendations button
        overlay.querySelector('#proceed-combined-pay-btn').addEventListener('click', () => {
            overlay.remove();
            if (selectedAddons.size === 0) {
                initiateCheckout(title, price, merchantId);
            } else {
                const addonTitles = Array.from(selectedAddons.keys());
                const combinedTitle = `${title} + ${addonTitles.join(' + ')}`;
                let currentTotalInr = baseInrPrice;
                selectedAddons.forEach((ap) => currentTotalInr += ap);
                initiateCheckout(combinedTitle, currentTotalInr, merchantId);
            }
        });

        // Fetch sub-millisecond recommendations from API
        try {
            const res = await fetch(`/api/recommendations?product=${encodeURIComponent(title)}&top_k=2`);
            if (res.ok) {
                const data = await res.json();
                const latencyBadge = overlay.querySelector('#rec-latency-badge');
                if (latencyBadge) {
                    latencyBadge.innerHTML = `⚡ Recommendation SLA: <strong>${data.latency_ms || 0.18} ms</strong>`;
                }

                const recsList = overlay.querySelector('#recommendations-list');
                if (recsList) {
                    recsList.innerHTML = '';

                    const recommendations = data.recommendations || [];
                    if (recommendations.length === 0) {
                        recsList.innerHTML = `<div style="text-align:center; padding: 15px; color:#94a3b8;">No add-ons found for this product.</div>`;
                        return;
                    }

                    recommendations.forEach((rec, idx) => {
                        const mp = rec.mongo_product || {};
                        const recTitle = mp.title || rec.product || 'Add-on Item';
                        const recBrand = mp.brand || 'ShopSmart';
                        const rawRecPrice = mp.price || 19.99;
                        const recInrPrice = rawRecPrice < 1000 ? rawRecPrice * 83.0 : rawRecPrice;
                        const recRating = mp.rating || 4.5;
                        const recReason = rec.reason || 'Frequently bought together with this product';
                        const recType = rec.recommendation_type || 'Frequently Bought Together';

                        const itemCard = document.createElement('div');
                        itemCard.style.cssText = `
                            background: rgba(255, 255, 255, 0.03);
                            border: 1px solid rgba(255, 255, 255, 0.08);
                            border-radius: 14px;
                            padding: 14px 16px;
                            display: flex;
                            align-items: center;
                            justify-content: space-between;
                            gap: 14px;
                            transition: all 0.2s;
                        `;

                        itemCard.innerHTML = `
                            <div style="display: flex; align-items: center; gap: 14px; flex: 1;">
                                <input type="checkbox" class="addon-checkbox" id="addon-check-${idx}" style="
                                    width: 20px;
                                    height: 20px;
                                    accent-color: #38bdf8;
                                    cursor: pointer;
                                    flex-shrink: 0;
                                ">
                                <div>
                                    <div style="display: flex; align-items: center; gap: 8px;">
                                        <span style="font-size: 10px; color: #7dd3fc; font-weight: 700; background: rgba(56, 189, 248, 0.12); padding: 2px 6px; border-radius: 4px; text-transform: uppercase;">
                                            ${recBrand}
                                        </span>
                                        <span style="font-size: 10px; color: #fbbf24; background: rgba(251, 191, 36, 0.12); padding: 2px 6px; border-radius: 4px; font-weight: 700;">
                                            ★ ${recRating}
                                        </span>
                                        <span style="font-size: 10px; color: #a7f3d0; background: rgba(16, 185, 129, 0.12); padding: 2px 6px; border-radius: 4px; font-weight: 600;">
                                            ${recType}
                                        </span>
                                    </div>
                                    <h4 style="font-size: 0.95rem; font-weight: 700; color: #ffffff; margin-top: 4px; line-height: 1.3;">
                                        ${recTitle}
                                    </h4>
                                    <div style="font-size: 11px; color: #94a3b8; margin-top: 2px;">
                                         ${recReason}
                                    </div>
                                </div>
                            </div>

                            <div style="text-align: right; flex-shrink: 0;">
                                <div style="font-family: 'Outfit', sans-serif; font-size: 1.15rem; font-weight: 800; color: #34d399;">
                                    +₹${recInrPrice.toFixed(2)}
                                </div>
                                <label for="addon-check-${idx}" style="font-size: 11px; color: #38bdf8; font-weight: 600; cursor: pointer;">
                                    Add to Order
                                </label>
                            </div>
                        `;

                        const chk = itemCard.querySelector('.addon-checkbox');
                        chk.addEventListener('change', (e) => {
                            if (e.target.checked) {
                                selectedAddons.set(recTitle, recInrPrice);
                                itemCard.style.borderColor = '#38bdf8';
                                itemCard.style.background = 'rgba(56, 189, 248, 0.06)';
                            } else {
                                selectedAddons.delete(recTitle);
                                itemCard.style.borderColor = 'rgba(255, 255, 255, 0.08)';
                                itemCard.style.background = 'rgba(255, 255, 255, 0.03)';
                            }
                            updateSummary();
                        });

                        recsList.appendChild(itemCard);
                    });
                }
            } else {
                const recsList = overlay.querySelector('#recommendations-list');
                if (recsList) {
                    recsList.innerHTML = `<div style="text-align:center; padding: 15px; color:#94a3b8;">No add-ons available for this product. You can proceed to checkout below.</div>`;
                }
            }
        } catch (err) {
            console.error('Failed to fetch recommendations for pre-checkout:', err);
            const recsList = overlay.querySelector('#recommendations-list');
            if (recsList) {
                recsList.innerHTML = `<div style="text-align:center; padding: 15px; color:#94a3b8;">Add-ons service unavailable. You can proceed to checkout below.</div>`;
            }
        }
    }

    /**
     * Initiate Official Razorpay Standard Checkout UI payment flow with MongoDB prefilled data
     */
    async function initiateCheckout(title, price, merchantId = '') {
        appendMessageToUI('user', `[Checkout] Opening official Razorpay checkout UI for "${title}"...`);
        const loader = appendLoaderToUI();

        try {
            await ensureRazorpayLoaded();

            // Fetch real merchant & profile details from MongoDB database via backend API
            let merchantInfo = {
                name: "ShopSmart Verified Vendor",
                email: "vendor@shopsmart.ai",
                phone: "9876543210",
                account_number: "7878780080316316"
            };

            try {
                const mRes = await fetch(`/api/merchant/info?merchant_id=${encodeURIComponent(merchantId || '')}`);
                if (mRes.ok) {
                    const data = await mRes.json();
                    if (data.success && data.merchant) {
                        merchantInfo = data.merchant;
                    }
                }
            } catch (e) {
                console.warn("Could not fetch merchant info from MongoDB, using defaults", e);
            }

            // Create official Razorpay Order via backend API using keys from .env
            let orderId = '';
            let razorpayKey = merchantInfo.razorpay_key_id || "rzp_test_TUm5bQm8NFvqYB";
            const parsedPrice = parseFloat(price) || 0;
            const inrPrice = parsedPrice < 1000 ? parsedPrice * 83.0 : parsedPrice;
            let amountInPaise = Math.round(inrPrice * 100);

            try {
                const orderRes = await fetch('/api/payment/create-order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_title: title,
                        price: parsedPrice,
                        merchant_id: merchantId
                    })
                });
                if (orderRes.ok) {
                    const orderData = await orderRes.json();
                    if (orderData.success) {
                        if (orderData.order_id) orderId = orderData.order_id;
                        if (orderData.key_id) razorpayKey = orderData.key_id;
                        if (orderData.amount) amountInPaise = orderData.amount;
                    }
                }
            } catch (e) {
                console.warn("Could not create Razorpay order on backend:", e);
            }

            loader.remove();

            const options = {
                key: razorpayKey,
                amount: amountInPaise,
                currency: "INR",
                order_id: orderId || undefined,
                name: merchantInfo.merchant_name || merchantInfo.name || "ShopSmart Merchant",
                description: `Payment for: ${title}`,
                image: "https://razorpay.com/favicon.ico",
                handler: async function (response) {
                    const payId = response.razorpay_payment_id || `pay_${Math.random().toString(36).substr(2, 9)}`;
                    const confirmedOrderId = response.razorpay_order_id || orderId || `order_${Math.random().toString(36).substr(2, 9)}`;
                    const sig = response.razorpay_signature || "official_rzp_success";

                    // Record verified transaction & trigger RazorpayX Payout on backend
                    let payoutId = '';
                    let payoutStatus = '';
                    try {
                        const verifyRes = await fetch('/api/payment/verify', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                order_id: confirmedOrderId,
                                payment_id: payId,
                                signature: sig,
                                product_title: title,
                                price: parsedPrice,
                                merchant_id: merchantId
                            })
                        });
                        if (verifyRes.ok) {
                            const vData = await verifyRes.json();
                            if (vData.payout) {
                                payoutId = vData.payout.payout_id || '';
                                payoutStatus = vData.payout.status || '';
                            }
                        }
                    } catch (err) {
                        console.warn('Error during payment verification/payout:', err);
                    }

                    const inrAmountStr = inrPrice.toFixed(2);
                    let payoutText = payoutId ? `\n• **RazorpayX Payout ID:** \`${payoutId}\`\n• **RazorpayX Status:** \`${payoutStatus || 'queued'}\`` : `\n• **RazorpayX Status:** \`Initiated on x.razorpay.com/payouts\``;

                    const successMsg = `🎉 **Razorpay Standard Checkout & Payout Successful!**\n\n` +
                        `📦 **Order Summary:**\n` +
                        `• **Product:** ${title}\n` +
                        `• **Amount Paid:** ₹${inrAmountStr}\n` +
                        `• **Beneficiary Vendor:** ${merchantInfo.merchant_name || merchantInfo.name || 'ShopSmart Verified Vendor'}\n` +
                        `• **Payment ID:** \`${payId}\`\n` +
                        `• **Order ID:** \`${confirmedOrderId}\`${payoutText}\n\n`;

                    appendMessageToUI('assistant', successMsg);
                    saveMessageToThread(currentThreadId, 'assistant', successMsg);

                    await sendDirectBotMessage(`Confirm successful checkout and payment of item "${title}" at ₹${inrAmountStr}.`);
                },
                modal: {
                    ondismiss: function () {
                        const cancelMsg = `ℹ️ Razorpay checkout modal was closed. You can retry anytime!`;
                        appendMessageToUI('assistant', cancelMsg);
                        saveMessageToThread(currentThreadId, 'assistant', cancelMsg);
                    }
                },
                prefill: {
                    name: merchantInfo.merchant_name || merchantInfo.name || "ShopSmart Customer",
                    email: merchantInfo.email || "vendor@shopsmart.ai",
                    contact: merchantInfo.phone || "9876543210",
                    method: "card"
                },
                notes: {
                    merchant_account: merchantInfo.account_number || "7878780080316316",
                    product_title: title
                },
                theme: {
                    color: "#0c2340"
                }
            };

            const rzp = new window.Razorpay(options);
            rzp.open();
        } catch (error) {
            if (loader) loader.remove();
            console.error('Failed to open Razorpay SDK UI:', error);
            appendMessageToUI('assistant', `Payment Failed Retry: ${error.message}`);
        }
    }



    async function sendDirectBotMessage(text) {
        const loader = appendLoaderToUI();
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    thread_id: currentThreadId
                })
            });
            loader.remove();

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let assistantText = '';

            const messageDiv = document.createElement('div');
            messageDiv.className = 'message assistant';
            const avatar = document.createElement('div');
            avatar.className = 'avatar';
            avatar.innerText = 'AI';
            messageDiv.appendChild(avatar);

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            const p = document.createElement('p');
            contentDiv.appendChild(p);
            messageDiv.appendChild(contentDiv);
            messagesContainer.appendChild(messageDiv);

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n');
                for (const line of lines) {
                    if (line.trim().startsWith('data:')) {
                        const data = JSON.parse(line.replace('data:', '').trim());
                        if (data.type === 'token') {
                            assistantText += data.content;
                            p.innerHTML = renderMarkdown(assistantText);
                            scrollToBottom();
                        }
                    }
                }
            }
            saveMessageToThread(currentThreadId, 'assistant', assistantText);
        } catch (e) {
            loader.remove();
        }
    }

    
    async function handleFormSubmit(e) {
        e.preventDefault();
        const text = chatInput.value.trim();
        if (!text) return;

        chatInput.value = '';

        // 1. Render User Message in UI
        appendMessageToUI('user', text);
        saveMessageToThread(currentThreadId, 'user', text);

        // Auto-generate dynamic LLM session title on first message
        const currentThread = threads.find(t => t.id === currentThreadId);
        if (currentThread && (!currentThread.title || currentThread.title.startsWith('Session ') || currentThread.title === 'New Chat')) {
            generateAndSetTitle(currentThread, text);
        }

        // 2. Render Typing Indicator
        const loader = appendLoaderToUI();

        // 3. Call FastAPI Backend Chat Endpoint (Stream)
        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    message: text,
                    thread_id: currentThreadId
                })
            });

            if (!response.ok) {
                throw new Error('API server returned error status');
            }

            // Remove typing loader immediately when stream starts
            loader.remove();

            // Create message placeholder for the streaming response
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message assistant';
            const avatar = document.createElement('div');
            avatar.className = 'avatar';
            avatar.innerText = 'AI';
            messageDiv.appendChild(avatar);

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            const p = document.createElement('p');
            contentDiv.appendChild(p);
            messageDiv.appendChild(contentDiv);
            messagesContainer.appendChild(messageDiv);
            scrollToBottom();

            // Read the stream
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let assistantText = '';
            let assistantProducts = [];

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep partial line in buffer

                for (const line of lines) {
                    if (line.trim().startsWith('data:')) {
                        const dataStr = line.replace('data:', '').trim();
                        try {
                            const data = JSON.parse(dataStr);
                            if (data.type === 'token') {
                                assistantText += data.content;
                                p.innerHTML = renderMarkdown(assistantText);
                                scrollToBottom();
                            } else if (data.type === 'products') {
                                assistantProducts = data.products || [];
                            } else if (data.type === 'error') {
                                assistantText += `\n[Error: ${data.content}]`;
                                p.innerHTML = renderMarkdown(assistantText);
                            }
                        } catch (err) {
                            console.error('Error parsing stream chunk:', err, dataStr);
                        }
                    }
                }
            }

            // Render product recommendations if any
            if (assistantProducts && assistantProducts.length > 0) {
                const tilesContainer = renderProductTilesContainer(assistantProducts);
                if (tilesContainer) {
                    contentDiv.appendChild(tilesContainer);
                }
                scrollToBottom();
            }

            saveMessageToThread(currentThreadId, 'assistant', assistantText, assistantProducts);

        } catch (error) {
            console.error(error);
            loader.remove();

            const errMsg = "Oops, I encountered a communication error with the backend server. Please verify your FastAPI backend is running and try again.";
            appendMessageToUI('assistant', errMsg);
            saveMessageToThread(currentThreadId, 'assistant', errMsg);
        }
    }
});
