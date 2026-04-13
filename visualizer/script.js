let baseImg;
let bangImg;

const apiUrl = 'http://localhost:8000/api/scores';
const fetchInterval = 200;
const participants = ['left', 'right'];
const fields = ['muse_score', 'gsr_z'];
const blendWeights = {
    muse_score: 0.50,
    gsr_z: 0.50,
};
const sliderFieldMap = {
    muse_score: { inputId: 'muse-score', valueId: 'muse-score-value', decimals: 0 },
    gsr_z: { inputId: 'gsr-z', valueId: 'gsr-z-value', decimals: 2 },
};

const state = {
    left: {
        muse_score: 50,
        gsr_z: 0,
        score: 50,
        render_bang_percent: 50,
        interaction: null,
        label: 'calibrating',
        status: 'offline',
    },
    right: {
        muse_score: 50,
        gsr_z: 0,
        score: 50,
        render_bang_percent: 50,
        interaction: null,
        label: 'calibrating',
        status: 'offline',
    },
};

let renderScale = 1;
let baseCanvasWidth = 0;
let baseCanvasHeight = 0;
const wigGap = 36;
let dataSourceMode = 'backend';

function preload() {
    baseImg = loadImage('wig base.png');
    bangImg = loadImage('wig bang.png');
}

function setup() {
    baseCanvasWidth = (baseImg.width * 2) + wigGap;
    baseCanvasHeight = baseImg.height;

    const container = document.getElementById('p5-container');
    const canvas = createCanvas(100, 100);
    canvas.parent(container);

    resizeCanvasToContainer();
    window.addEventListener('resize', resizeCanvasToContainer);

    setupDataControls();
    setupSliderBindings();
    refreshSliderUIFromState();
    recomputeManualBlend('left');
    recomputeManualBlend('right');

    updatePanels();
    fetchDualScores();
    setInterval(fetchDualScores, fetchInterval);
}

function setupDataControls() {
    const sourceSelect = document.getElementById('data-source');
    const resetButton = document.getElementById('reset-values');

    sourceSelect.addEventListener('change', event => {
        dataSourceMode = event.target.value;
        syncEditorModeUI();

        if (dataSourceMode === 'manual') {
            updateConnectionStatus('Manual mode');
            state.left.status = 'manual';
            state.right.status = 'manual';
            updatePanels();
        }
    });

    resetButton.addEventListener('click', () => {
        for (const participantId of participants) {
            state[participantId].muse_score = 50;
            state[participantId].gsr_z = 0;
            state[participantId].score = 50;
            state[participantId].render_bang_percent = 50;
            if (dataSourceMode === 'manual') {
                state[participantId].label = 'calibrating';
                state[participantId].status = 'manual';
                recomputeManualBlend(participantId);
            }
        }
        refreshSliderUIFromState();
        updatePanels();
    });

    syncEditorModeUI();
}

function setupSliderBindings() {
    for (const participantId of participants) {
        for (const field of fields) {
            const map = sliderFieldMap[field];
            const input = document.getElementById(`${participantId}-${map.inputId}`);
            input.addEventListener('input', event => {
                const nextValue = Number(event.target.value);
                state[participantId][field] = normalizeFieldValue(field, nextValue);
                updateSingleSliderValueLabel(participantId, field, state[participantId][field]);

                if (dataSourceMode === 'manual') {
                    recomputeManualBlend(participantId);
                    state[participantId].status = 'manual';
                    updatePanels();
                }
            });
        }
    }
}

function normalizeFieldValue(field, value) {
    if (field === 'gsr_z') {
        return Math.max(-3, Math.min(3, value));
    }
    if (field === 'gsr_value') {
        return Math.max(0, Math.min(65535, value));
    }
    return Math.max(0, Math.min(100, value));
}

function labelFromScore(score) {
    if (score < 35) {
        return 'calm';
    }
    if (score < 55) {
        return 'mild';
    }
    if (score < 75) {
        return 'moderate';
    }
    return 'high';
}

function refreshSliderUIFromState() {
    for (const participantId of participants) {
        for (const field of fields) {
            const map = sliderFieldMap[field];
            const input = document.getElementById(`${participantId}-${map.inputId}`);
            input.value = state[participantId][field];
            updateSingleSliderValueLabel(participantId, field, state[participantId][field]);
        }
    }
}

function recomputeManualBlend(participantId) {
    const participant = state[participantId];
    const interaction = computeInteraction(participant);
    participant.interaction = interaction;
    participant.render_bang_percent = interaction.blended;

    participant.label = labelFromScore(participant.render_bang_percent);
}

function toPercentScale(participant) {
    return {
        muse_score: normalizeFieldValue('muse_score', participant.muse_score),
        gsr_z: normalizeFieldValue('score', ((participant.gsr_z + 3) / 6) * 100),
    };
}

function computeInteraction(participant) {
    const scaled = toPercentScale(participant);
    const contributions = {
        muse_score: blendWeights.muse_score * scaled.muse_score,
        gsr_z: blendWeights.gsr_z * scaled.gsr_z,
    };

    const blended = normalizeFieldValue(
        'score',
        contributions.muse_score
        + contributions.gsr_z
    );

    return {
        scaled,
        contributions,
        blended,
    };
}

function updateInteractionUI(participantId) {
    const participant = state[participantId];
    const interaction = participant.interaction || computeInteraction(participant);
    participant.interaction = interaction;

    const formulaEl = document.getElementById(`${participantId}-interaction-formula`);
    if (formulaEl) {
        formulaEl.textContent = `0.50*M + 0.50*Z = ${interaction.blended.toFixed(1)}%`;
    }

    const uiMap = {
        muse_score: 'muse',
        gsr_z: 'gsrz',
    };

    for (const [field, suffix] of Object.entries(uiMap)) {
        const contribution = interaction.contributions[field];
        const contribEl = document.getElementById(`${participantId}-contrib-${suffix}`);
        const contribValueEl = document.getElementById(`${participantId}-contrib-${suffix}-value`);
        if (contribEl) contribEl.style.width = `${contribution}%`;
        if (contribValueEl) contribValueEl.textContent = contribution.toFixed(1);
    }
}

function updateSingleSliderValueLabel(participantId, field, value) {
    const map = sliderFieldMap[field];
    const label = document.getElementById(`${participantId}-${map.valueId}`);
    label.textContent = Number(value).toFixed(map.decimals);
}

function syncEditorModeUI() {
    const isManual = dataSourceMode === 'manual';
    const resetButton = document.getElementById('reset-values');

    for (const participantId of participants) {
        for (const field of fields) {
            const map = sliderFieldMap[field];
            const input = document.getElementById(`${participantId}-${map.inputId}`);
            input.disabled = !isManual;
        }
    }

    resetButton.disabled = !isManual;
}

function updateConnectionStatus(message) {
    document.getElementById('connection-status').textContent = message;
}

function applyBackendPayloadToState(payload) {
    for (const participantId of participants) {
        const incoming = payload?.participants?.[participantId] || {};
        state[participantId].muse_score = normalizeFieldValue('muse_score', Number(incoming.muse_score ?? 50));
        state[participantId].gsr_z = normalizeFieldValue('gsr_z', Number(incoming.gsr_z ?? 0));
        state[participantId].score = normalizeFieldValue('score', Number(incoming.score ?? 50));
        state[participantId].render_bang_percent = normalizeFieldValue(
            'score',
            Number(incoming.bang_down_percent ?? incoming.score ?? 50),
        );
        state[participantId].interaction = computeInteraction(state[participantId]);
        state[participantId].label = incoming.label || labelFromScore(state[participantId].score);
        state[participantId].status = incoming.status || 'offline';
    }

    refreshSliderUIFromState();
}

function resizeCanvasToContainer() {
    const container = document.getElementById('p5-container');
    const availableWidth = container.clientWidth;
    renderScale = Math.min(1.0, availableWidth / baseCanvasWidth);
    resizeCanvas(baseCanvasWidth * renderScale, baseCanvasHeight * renderScale);
}

function fetchDualScores() {
    if (dataSourceMode !== 'backend') {
        return;
    }

    fetch(apiUrl)
        .then(response => response.json())
        .then(payload => {
            if (!payload.participants) {
                return;
            }

            applyBackendPayloadToState(payload);
            updateConnectionStatus('Connected');
            updatePanels();
        })
        .catch(() => {
            updateConnectionStatus('Backend unreachable');

            if (dataSourceMode === 'backend') {
                dataSourceMode = 'manual';
                document.getElementById('data-source').value = 'manual';
                syncEditorModeUI();
                state.left.status = 'manual';
                state.right.status = 'manual';
                recomputeManualBlend('left');
                recomputeManualBlend('right');
                updatePanels();
            }
        });
}

function updatePanels() {
    for (const participantId of participants) {
        const participant = state[participantId];
        const scoreEl = document.getElementById(`${participantId}-score`);
        const blendEl = document.getElementById(`${participantId}-blend`);
        const labelEl = document.getElementById(`${participantId}-label`);
        const statusEl = document.getElementById(`${participantId}-status`);
        
        if (scoreEl) scoreEl.textContent = Math.round(participant.score);
        if (blendEl) blendEl.textContent = participant.render_bang_percent.toFixed(1);
        if (labelEl) labelEl.textContent = participant.label;
        if (statusEl) statusEl.textContent = participant.status;
        updateInteractionUI(participantId);
    }
}

function drawWigAt(xOffset, participantState) {
    image(baseImg, xOffset, 0);

    const percentageToShow = participantState.render_bang_percent / 100;
    const bangHeight = bangImg.height;
    const heightToShow = bangHeight * percentageToShow;

    image(
        bangImg,
        xOffset,
        0,
        bangImg.width,
        heightToShow,
        0,
        0,
        bangImg.width,
        heightToShow,
    );
}

function draw() {
    clear();
    push();
    scale(renderScale);

    drawWigAt(0, state.left);
    drawWigAt(baseImg.width + wigGap, state.right);

    pop();
}
