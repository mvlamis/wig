let baseImg;
let bangImg;

const apiUrl = 'http://localhost:8000/api/scores';
const weightApiUrl = 'http://localhost:8000/api/weights';
const fetchInterval = 200;
const weightFetchInterval = 2000;
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
let weightSaveTimeout = null;

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
    setupWeightControls();
    setupSliderBindings();
    refreshSliderUIFromState();
    refreshWeightUI();
    recomputeManualBlend('left');
    recomputeManualBlend('right');

    updatePanels();
    fetchDualScores();
    fetchWeights();
    setInterval(fetchDualScores, fetchInterval);
    setInterval(fetchWeights, weightFetchInterval);
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
        } else {
            fetchWeights();
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

function setupWeightControls() {
    const museInput = document.getElementById('weight-muse');
    const gsrInput = document.getElementById('weight-gsr');
    const refreshButton = document.getElementById('refresh-weights');

    museInput.addEventListener('input', event => {
        const musePct = normalizeWeightPercent(Number(event.target.value));
        blendWeights.muse_score = musePct / 100;
        blendWeights.gsr_z = (100 - musePct) / 100;
        refreshWeightUI();
        handleWeightChanged();
    });

    gsrInput.addEventListener('input', event => {
        const gsrPct = normalizeWeightPercent(Number(event.target.value));
        blendWeights.gsr_z = gsrPct / 100;
        blendWeights.muse_score = (100 - gsrPct) / 100;
        refreshWeightUI();
        handleWeightChanged();
    });

    refreshButton.addEventListener('click', () => {
        fetchWeights();
    });
}

function normalizeWeightPercent(value) {
    return Math.max(0, Math.min(100, Math.round(value)));
}

function refreshWeightUI() {
    const musePct = normalizeWeightPercent(blendWeights.muse_score * 100);
    const gsrPct = 100 - musePct;

    document.getElementById('weight-muse').value = musePct;
    document.getElementById('weight-gsr').value = gsrPct;
    document.getElementById('weight-muse-value').textContent = `${musePct}%`;
    document.getElementById('weight-gsr-value').textContent = `${gsrPct}%`;

    for (const participantId of participants) {
        recomputeManualBlend(participantId);
    }

    updatePanels();
}

function handleWeightChanged() {
    if (dataSourceMode === 'backend') {
        setWeightStatus('Saving weights...');
        queueWeightSave();
    } else {
        setWeightStatus('Manual mode weights updated locally');
    }
}

function queueWeightSave() {
    if (weightSaveTimeout) {
        clearTimeout(weightSaveTimeout);
    }

    weightSaveTimeout = setTimeout(() => {
        saveWeightsToBackend();
    }, 120);
}

function setWeightStatus(message) {
    document.getElementById('weight-status').textContent = message;
}

function applyWeightPayload(payload) {
    const museWeight = Number(payload?.muse_weight);
    const gsrWeight = Number(payload?.gsr_weight);
    if (!Number.isFinite(museWeight) || !Number.isFinite(gsrWeight)) {
        return false;
    }

    const total = museWeight + gsrWeight;
    if (total <= 0) {
        return false;
    }

    blendWeights.muse_score = museWeight / total;
    blendWeights.gsr_z = gsrWeight / total;
    refreshWeightUI();
    return true;
}

function fetchWeights() {
    if (dataSourceMode !== 'backend') {
        return;
    }

    fetch(weightApiUrl)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to fetch weights');
            }
            return response.json();
        })
        .then(payload => {
            const applied = applyWeightPayload(payload);
            if (applied) {
                setWeightStatus('Live backend control active');
            } else {
                setWeightStatus('Backend returned invalid weights');
            }
        })
        .catch(() => {
            setWeightStatus('Weight API unavailable');
        });
}

function saveWeightsToBackend() {
    if (dataSourceMode !== 'backend') {
        return;
    }

    fetch(weightApiUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            muse_weight: blendWeights.muse_score,
            gsr_weight: blendWeights.gsr_z,
        }),
    })
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to save weights');
            }
            return response.json();
        })
        .then(payload => {
            const applied = applyWeightPayload(payload);
            if (applied) {
                setWeightStatus('Weights saved');
            } else {
                setWeightStatus('Weights saved, but payload was invalid');
            }
        })
        .catch(() => {
            setWeightStatus('Failed to save weights');
        });
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
        formulaEl.textContent = `${blendWeights.muse_score.toFixed(2)}*M + ${blendWeights.gsr_z.toFixed(2)}*Z = ${interaction.blended.toFixed(1)}%`;
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
    const museWeightInput = document.getElementById('weight-muse');
    const gsrWeightInput = document.getElementById('weight-gsr');
    const refreshWeightsButton = document.getElementById('refresh-weights');

    for (const participantId of participants) {
        for (const field of fields) {
            const map = sliderFieldMap[field];
            const input = document.getElementById(`${participantId}-${map.inputId}`);
            input.disabled = !isManual;
        }
    }

    resetButton.disabled = !isManual;
    museWeightInput.disabled = false;
    gsrWeightInput.disabled = false;
    refreshWeightsButton.disabled = isManual;
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
