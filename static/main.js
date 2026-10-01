document.addEventListener("DOMContentLoaded", () => {
    const chatWindow = document.getElementById("chat-window");
    const messageInput = document.getElementById("message-input");
    const sendButton = document.getElementById("send-button");
    const sourceDropdown = document.getElementById("source-dropdown");

    // 웹소켓 연결
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

    function addMessage(type, content, id = null) {
        const chatWindow = document.getElementById('chat-window');

        // Wrapper for alignment and profile image
        const wrapperDiv = document.createElement('div');
        // type is 'bot', 'user', 'error', 'info'
        // Map to class names: 'bot-message', 'user-message'

        let messageClass = '';
        if (type === 'bot') messageClass = 'bot-message';
        else if (type === 'user') messageClass = 'user-message';
        else if (type === 'error') messageClass = 'error-message';
        else messageClass = 'info-message';

        const isBot = type === 'bot';
        const isUser = type === 'user';

        wrapperDiv.classList.add('message-wrapper');
        if (isBot) wrapperDiv.classList.add('bot-wrapper');
        else if (isUser) wrapperDiv.classList.add('user-wrapper');
        else wrapperDiv.style.justifyContent = 'center'; // Center system/error messages

        // Add Profile Image for Bot
        if (isBot) {
            const profileImg = document.createElement('img');
            profileImg.src = '/static/resources/chat_profile.png';
            profileImg.classList.add('profile-img');
            wrapperDiv.appendChild(profileImg);
        }

        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', messageClass);
        if (id) {
            messageDiv.id = id;
        }

        if (isBot && typeof content === 'object') {
            const answerDiv = document.createElement('div');
            let formattedAnswer = content.answer.replace(/\*\*/g, '');

            // Only replace newlines with <br> if the content is NOT HTML (doesn't start with <)
            if (!formattedAnswer.trim().startsWith('<')) {
                formattedAnswer = formattedAnswer.replace(/\n/g, '<br>');
            }

            answerDiv.innerHTML = formattedAnswer;
            messageDiv.appendChild(answerDiv);

            if (content.sources && content.answer !== "현재 설정된 문서에 관련 내용이 없어 답변할 수 없습니다." && content.answer !== "제공된 문서에 관련 내용이 없어 답변할 수 없습니다.") {
                const sourcesDiv = document.createElement('div');
                sourcesDiv.classList.add('sources');
                sourcesDiv.textContent = `참고: ${content.sources}`;
                messageDiv.appendChild(sourcesDiv);
            }
        } else {
            // For simple text messages
            if (typeof content === 'string' && content.trim().startsWith('<')) {
                messageDiv.innerHTML = content;
            } else {
                messageDiv.innerHTML = typeof content === 'string' ? content.replace(/\n/g, '<br>') : content;
            }
        }

        if (isBot) {
            // New structure: Wrapper -> [ProfileImg, ContentCol]
            // ContentCol -> [MessageBubble, QuickReplies]

            const contentCol = document.createElement('div');
            contentCol.style.display = 'flex';
            contentCol.style.flexDirection = 'column';
            contentCol.style.alignItems = 'flex-start';
            contentCol.style.maxWidth = 'calc(100% - 50px)'; // Adjust for profile image width + gap

            contentCol.appendChild(messageDiv);

            if (content.quickReplies) {
                const qrContainer = document.createElement('div');
                qrContainer.className = 'quick-replies';

                content.quickReplies.forEach(text => {
                    const btn = document.createElement('button');
                    btn.className = 'quick-reply-btn';
                    btn.textContent = text;
                    qrContainer.appendChild(btn);
                });
                contentCol.appendChild(qrContainer);
            }
            wrapperDiv.appendChild(contentCol);
        } else {
            wrapperDiv.appendChild(messageDiv);
        }

        chatWindow.appendChild(wrapperDiv);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    // --- 필터 로직 ---
    const filterContainer = document.getElementById("filter-container");
    const dropdownBtn = document.getElementById("dropdown-btn");

    // 드롭다운 토글 기능
    dropdownBtn.addEventListener("click", () => {
        filterContainer.classList.toggle("show");
    });

    // 외부 클릭 시 닫기
    window.addEventListener("click", (e) => {
        if (!e.target.matches('.dropdown-btn') && !e.target.closest('.dropdown-content')) {
            if (filterContainer.classList.contains('show')) {
                filterContainer.classList.remove('show');
            }
        }
    });

    async function fetchFilters() {
        try {
            const response = await fetch('/api/filters');
            if (!response.ok) throw new Error('필터 목록 실패');
            const data = await response.json();
            renderFilters(data.batches);
            updateDropdownText(); // 초기 로드 시 드롭다운 텍스트 업데이트
        } catch (error) {
            console.error(error);
            addMessage("error", "필터 정보를 불러오지 못했습니다.");
        }
    }

    function renderFilters(batches) {
        if (!batches) return;

        filterContainer.innerHTML = ''; // 초기화

        // 1. "모두 선택" 체크박스 생성
        const selectAllLabel = document.createElement("label");
        selectAllLabel.className = "select-all-label";

        const selectAllCheckbox = document.createElement("input");
        selectAllCheckbox.type = "checkbox";
        selectAllCheckbox.id = "select-all-checkbox";
        selectAllCheckbox.style.marginRight = "10px";

        selectAllLabel.appendChild(selectAllCheckbox);
        selectAllLabel.appendChild(document.createTextNode("전체 기수 선택"));
        filterContainer.appendChild(selectAllLabel);

        // 2. 개별 기수 체크박스 생성
        const itemCheckboxes = [];
        batches.forEach(item => {
            const label = document.createElement("label");
            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.value = item.batch;
            checkbox.className = "batch-item-checkbox";
            checkbox.style.marginRight = "10px";

            // 텍스트 업데이트 및 모두 선택 체크박스 상태 동기화
            checkbox.addEventListener("change", () => {
                updateDropdownText();
                syncSelectAllState();
            });

            label.appendChild(checkbox);
            label.appendChild(document.createTextNode(`제${item.batch}기 (${item.years})`));

            filterContainer.appendChild(label);
            itemCheckboxes.push(checkbox);
        });

        // "모두 선택" 이벤트 리스너
        selectAllCheckbox.addEventListener("change", (e) => {
            const isChecked = e.target.checked;
            itemCheckboxes.forEach(cb => cb.checked = isChecked);
            updateDropdownText();
        });

        // 3. 적용 버튼 (하단 고정)
        const controlPanel = document.createElement("div");
        controlPanel.className = "dropdown-controls";

        const applyBtn = document.createElement("button");
        applyBtn.textContent = "적용";
        applyBtn.className = "control-btn apply-btn";
        applyBtn.addEventListener("click", () => {
            sendFilterUpdate();
            filterContainer.classList.remove("show"); // 적용 후 닫기
        });

        controlPanel.appendChild(applyBtn);
        filterContainer.appendChild(controlPanel);
    }

    function syncSelectAllState() {
        const selectAllCheckbox = document.getElementById("select-all-checkbox");
        const itemCheckboxes = document.querySelectorAll(".batch-item-checkbox");
        const allChecked = Array.from(itemCheckboxes).every(cb => cb.checked);
        const someChecked = Array.from(itemCheckboxes).some(cb => cb.checked);

        selectAllCheckbox.checked = allChecked;
        // (선택사항) 일부만 선택되었을 때 indeterminate 상태 표시 가능: selectAllCheckbox.indeterminate = someChecked && !allChecked;
    }

    function toggleAllCheckboxes(checked) {
        // Deprecated by select-all checkbox logic above, but keeping for reference if needed or removing.
        // The logic is now inside the selectAllCheckbox change listener.
    }

    function updateDropdownText() {
        const checkboxes = filterContainer.querySelectorAll(".batch-item-checkbox");
        const selected = Array.from(checkboxes).filter(cb => cb.checked);

        if (selected.length === 0) {
            dropdownBtn.textContent = "검색 대상 기수 선택 (전체)";
        } else {
            const batches = selected.map(cb => `${cb.value}기`).join(", ");
            if (selected.length > 2) {
                dropdownBtn.textContent = `${selected.length}개 기수 선택됨 (${batches.substring(0, 10)}...)`;
            } else {
                dropdownBtn.textContent = `${batches} 선택됨`;
            }
        }
    }

    function sendFilterUpdate() {
        if (socket.readyState !== WebSocket.OPEN) return;

        const checkboxes = filterContainer.querySelectorAll(".batch-item-checkbox");
        const selectedBatches = Array.from(checkboxes)
            .filter(cb => cb.checked)
            .map(cb => parseInt(cb.value));

        const messagePayload = {
            type: "filter_change",
            content: selectedBatches
        };
        socket.send(JSON.stringify(messagePayload));
    }

    socket.onopen = () => {
        console.log("웹소켓 연결 성공");
        // 초기 웰컴 메시지는 서버에서 "system"으로 오지만, 우리가 직접 예쁘게 꾸며서 보여주고 싶으므로 
        // 서버 메시지를 무시하거나, 여기서 직접 추가할 수 있음.
        // 여기서는 서버가 보내는 "chat ready" 메시지를 무시하고 클라이언트에서 직접 가이드 메시지를 띄움.
    };

    socket.onmessage = (event) => {
        const message = JSON.parse(event.data);
        console.log("서버로부터 메시지 수신:", message);

        const loadingMessage = document.getElementById("loading-message");
        if (loadingMessage) {
            loadingMessage.remove();
        }

        switch (message.type) {
            case "system":
                // 사용자가 시스템 메시지(특히 필터 변경 등)가 안 보이길 원함.
                // 단, "챗봇이 준비되었습니다" 같은 초기 메시지는 필요할 수 있으나, 
                // 위요청에 따라 "Guide Message" 형태로 대체하기 위해 여기서는 무시하거나 특정 메시지만 허용.
                if (message.data.includes("준비되었습니다")) {
                    const guideContent = `
                        <div>안녕하세요!👋<br><br>국민건강영양조사 챗봇입니다. 궁금한 점을 자유롭게 물어보세요.<br><br></div>
                        <ul style="margin-top: 10px; padding-left: 20px; font-size: 0.9em; color: #555;">
                            <li>"이 조사는 어떤 법에 따라 실시돼?"</li>
                            <li>"3기에서 지역구분 변수 코드는?"</li>
                            <li>"결측값 코딩은 어떻게 되어 있어?"</li>
                        </ul>
                    `;
                    addMessage("bot", {
                        answer: guideContent,
                        sources: null,
                        quickReplies: [
                            "🚍 조사는 어디서 해?",
                            "👥 누가 뽑히는 거야?",
                            "🏥 어떤 검사를 해?",
                            "🔄 조사 절차 한눈에 보기"
                        ]
                    });
                }
                // 그 외 필터 변경 등의 시스템 메시지는 출력하지 않음 (Suppress)
                break;
            case "answer":
                addMessage("bot", message.data);
                break;
            case "error":
                addMessage("error", message.data);
                break;
            default:
                console.warn("알 수 없는 메시지 타입:", message.type);
        }
    };

    function sendMessage() {
        const messageText = messageInput.value.trim();
        if (messageText && socket.readyState === WebSocket.OPEN) {
            addMessage("user", messageText);
            const messagePayload = {
                type: "question",
                content: messageText
            };
            socket.send(JSON.stringify(messagePayload));
            messageInput.value = "";
            addMessage("info", "답변을 생성중입니다...", "loading-message");
        }
    }

    sendButton.addEventListener("click", sendMessage);
    messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            sendMessage();
        }
    });

    // Quick Reply Click Handler (Event Delegation)
    document.addEventListener("click", (e) => {
        if (e.target.classList.contains("quick-reply-btn")) {
            const btnText = e.target.textContent;

            // Map casual UI text to precise backend queries for better RAG performance
            const queryMap = {
                "🚍 조사는 어디서 해?": "국민건강영양조사 이동검진차량 및 조사 장소에 대한 정보",
                "👥 누가 뽑히는 거야?": "국민건강영양조사 표본 선정 기준 및 대상자 선정 방식",
                "🏥 어떤 검사를 해?": "국민건강영양조사 검진 항목, 건강설문, 영양조사 내용 상세",
                "🔄 조사 절차 한눈에 보기": "국민건강영양조사 조사 수행 절차 및 흐름"
            };

            const actualQuery = queryMap[btnText] || btnText;

            // UI: Show the casual button text (Friendly)
            addMessage("user", btnText);

            // Backend: Send the precise query (Smart)
            const messagePayload = {
                type: "question",
                content: actualQuery
            };
            socket.send(JSON.stringify(messagePayload));
            addMessage("info", "답변을 생성중입니다...", "loading-message");
        }
    });

    // 초기화: 필터 목록 가져오기
    fetchFilters();
});