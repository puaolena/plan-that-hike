document.addEventListener('DOMContentLoaded', () => {

    // ─── Element References ───────────────────────────────────────────────────

    // API Key Settings
    const statusDot        = document.getElementById('statusDot');
    const statusText       = document.getElementById('statusText');
    const openSettingsBtn  = document.getElementById('openSettingsBtn');
    const settingsModal    = document.getElementById('settingsModal');
    const closeSettingsBtn = document.getElementById('closeSettingsBtn');
    const saveSettingsBtn  = document.getElementById('saveSettingsBtn');
    const apiKeyInput      = document.getElementById('apiKeyInput');

    // Search
    const searchForm        = document.getElementById('searchForm');
    const destinationInput  = document.getElementById('destinationInput');
    const searchBtn         = document.getElementById('searchBtn');
    const searchSpinner     = document.getElementById('searchSpinner');
    const ambiguityPanel    = document.getElementById('ambiguityPanel');
    const suggestionList    = document.getElementById('suggestionList');

    // Facts Card & banners
    const factsCard             = document.getElementById('factsCard');
    const factsSourceBadge      = document.getElementById('factsSourceBadge');
    const weatherTimeoutBanner  = document.getElementById('weatherTimeoutBanner');
    const weatherTimeoutMsg     = document.getElementById('weatherTimeoutMsg');
    const retryWeatherBtn       = document.getElementById('retryWeatherBtn');
    const retryBtnLabel         = document.getElementById('retryBtnLabel');
    const weatherFailBanner     = document.getElementById('weatherFailBanner');
    const weatherStaleBanner    = document.getElementById('weatherStaleBanner');
    const weatherStaleMsg       = document.getElementById('weatherStaleMsg');
    const trailMissingBanner    = document.getElementById('trailMissingBanner');
    const manualWeatherPanel    = document.getElementById('manualWeatherPanel');
    const weatherEstimateBtns   = document.getElementById('weatherEstimateBtns');
    const weatherSourceLabel    = document.getElementById('weatherSourceLabel');

    // Editable Fact Inputs
    const factName      = document.getElementById('factName');
    const factDistance  = document.getElementById('factDistance');
    const factElevation = document.getElementById('factElevation');
    const factTrailhead = document.getElementById('factTrailhead');
    const factWeather   = document.getElementById('factWeather');
    const factSunrise   = document.getElementById('factSunrise');
    const factSunset    = document.getElementById('factSunset');
    const factTerrain   = document.getElementById('factTerrain');

    // User Preference Inputs
    const hikeDay       = document.getElementById('hikeDay');
    const startTime     = document.getElementById('startTime');
    const hikePace      = document.getElementById('hikePace');
    const footwearPref  = document.getElementById('footwearPref');

    // Generate button
    const generateForm    = document.getElementById('generateForm');
    const generateBtn     = document.getElementById('generateBtn');
    const generateSpinner = document.getElementById('generateSpinner');

    // Recommendation Card
    const recommendationPlaceholder = document.getElementById('recommendationPlaceholder');
    const recommendationCard        = document.getElementById('recommendationCard');
    const resetBtn                  = document.getElementById('resetBtn');
    const hikeSummaryText           = document.getElementById('hikeSummaryText');
    const confidenceBanner          = document.getElementById('confidenceBanner');
    const confidenceBannerText      = document.getElementById('confidenceBannerText');

    // Metrics
    const valDuration  = document.getElementById('valDuration');
    const lblPace      = document.getElementById('lblPace');
    const valWater     = document.getElementById('valWater');
    const lblWaterRate = document.getElementById('lblWaterRate');
    const valSunset    = document.getElementById('valSunset');
    const lblSunset    = document.getElementById('lblSunset');

    // Warning Bar
    const warningBar  = document.getElementById('warningBar');
    const warningText = document.getElementById('warningText');

    // Lists
    const listMust  = document.getElementById('listMust');
    const listRec   = document.getElementById('listRec');
    const listOpt   = document.getElementById('listOpt');
    const countMust = document.getElementById('countMust');
    const countRec  = document.getElementById('countRec');
    const countOpt  = document.getElementById('countOpt');

    // Explanations
    const reportDuration = document.getElementById('reportDuration');
    const reportWater    = document.getElementById('reportWater');
    const reportSafety   = document.getElementById('reportSafety');

    // Progress tracker
    const stepEls = [
        document.getElementById('step1'),
        document.getElementById('step2'),
        document.getElementById('step3')
    ];
    const lineEls = [
        document.getElementById('line1'),
        document.getElementById('line2')
    ];

    // Custom item add
    const customItemInput   = document.getElementById('customItemInput');
    const addCustomItemBtn  = document.getElementById('addCustomItemBtn');
    const addOptionalItemRow = document.getElementById('addOptionalItemRow');

    // ─── State ───────────────────────────────────────────────────────────────

    let currentTrailFacts     = null;
    let weatherStatus         = 'success';   // 'success' | 'timeout' | 'failed' | 'stale'
    let weatherIsUserProvided = false;
    let retryAttempt          = 1;
    let retryTimer            = null;
    let currentDestination    = '';
    let customOptionalItems   = [];   // tracks user-added optional items

    // ─── Progress Tracker ────────────────────────────────────────────────────

    function setProgressStep(stepIndex) {
        // stepIndex: 1, 2, or 3
        stepEls.forEach((el, i) => {
            el.classList.remove('active', 'done');
            if (i + 1 < stepIndex)  el.classList.add('done');
            if (i + 1 === stepIndex) el.classList.add('active');
        });
        lineEls.forEach((el, i) => {
            el.classList.toggle('done', i + 1 < stepIndex);
        });
    }

    // Override done step bubble text to show checkmark via CSS
    // The ::after pseudo-element handles the check, hide the number
    function syncDoneBubbles() {
        stepEls.forEach(el => {
            const bubble = el.querySelector('.step-bubble');
            if (!bubble) return;
            if (el.classList.contains('done')) {
                bubble.dataset.original = bubble.dataset.original || bubble.textContent;
                bubble.textContent = '';
            } else {
                if (bubble.dataset.original) bubble.textContent = bubble.dataset.original;
            }
        });
    }

    function advanceProgress(step) {
        setProgressStep(step);
        syncDoneBubbles();
    }

    // Start at step 1
    advanceProgress(1);

    // ─── API Key Management ───────────────────────────────────────────────────

    function getApiKey() {
        return sessionStorage.getItem('gemini_api_key') || '';
    }

    async function checkApiKeyStatus() {
        try {
            const res  = await fetch('/api/check-key');
            const data = await res.json();
            if (data.has_key) {
                statusDot.className  = 'status-indicator green';
                statusText.textContent = 'Gemini Key Active (Env)';
            } else {
                const sessionKey = getApiKey();
                if (sessionKey) {
                    statusDot.className  = 'status-indicator green';
                    statusText.textContent = 'Gemini Key Active (Session)';
                    apiKeyInput.value = sessionKey;
                } else {
                    statusDot.className  = 'status-indicator yellow';
                    statusText.textContent = 'API Key Missing';
                }
            }
        } catch {
            statusDot.className  = 'status-indicator red';
            statusText.textContent = 'Server Offline';
        }
    }

    openSettingsBtn.addEventListener('click', () => settingsModal.classList.remove('hidden'));
    closeSettingsBtn.addEventListener('click', () => settingsModal.classList.add('hidden'));

    saveSettingsBtn.addEventListener('click', () => {
        const key = apiKeyInput.value.trim();
        if (key) sessionStorage.setItem('gemini_api_key', key);
        else      sessionStorage.removeItem('gemini_api_key');
        settingsModal.classList.add('hidden');
        checkApiKeyStatus();
    });

    checkApiKeyStatus();

    // ─── Banner Helpers ───────────────────────────────────────────────────────

    function hideAllBanners() {
        weatherTimeoutBanner.classList.add('hidden');
        weatherFailBanner.classList.add('hidden');
        weatherStaleBanner.classList.add('hidden');
        trailMissingBanner.classList.add('hidden');
        manualWeatherPanel.classList.add('hidden');
    }

    function setWeatherSourceLabel(source) {
        weatherSourceLabel.textContent = source;
        weatherSourceLabel.className = 'label-badge';
        if (source === 'Fetched') {
            weatherSourceLabel.classList.add('badge-fetched');
        } else if (source === 'Stale') {
            weatherSourceLabel.classList.add('badge-low-confidence');
        } else if (source === 'User Estimate') {
            weatherSourceLabel.classList.add('badge-user');
        } else {
            weatherSourceLabel.classList.add('badge-inferred');
        }
    }

    // Show the appropriate banners based on returned facts
    function applyWeatherBanners(facts) {
        hideAllBanners();
        weatherIsUserProvided = false;
        weatherStatus = facts.weather_status || 'success';

        const isTrailMissing = !facts.found || facts.trail_status === 'missing';

        if (isTrailMissing) {
            trailMissingBanner.classList.remove('hidden');
        }

        if (weatherStatus === 'timeout') {
            weatherTimeoutBanner.classList.remove('hidden');
            const attempt = retryAttempt;
            weatherTimeoutMsg.textContent =
                facts.weather_error
                    ? `${facts.weather_error} — Weather could not be loaded.`
                    : `Attempt ${attempt} failed. Weather service timed out.`;
            manualWeatherPanel.classList.remove('hidden');
            setWeatherSourceLabel('Unavailable');
            weatherIsUserProvided = true;

        } else if (weatherStatus === 'failed') {
            weatherFailBanner.classList.remove('hidden');
            manualWeatherPanel.classList.remove('hidden');
            setWeatherSourceLabel('Unavailable');
            weatherIsUserProvided = true;

        } else if (weatherStatus === 'stale') {
            weatherStaleBanner.classList.remove('hidden');
            setWeatherSourceLabel('Stale');

        } else {
            // Normal success
            setWeatherSourceLabel('Fetched');
        }
    }

    // ─── Exponential Backoff Retry ────────────────────────────────────────────

    // Max 3 attempts: delays ~ 1s, 2s (then give up)
    const RETRY_DELAYS = [1000, 2000];

    async function retryWithBackoff(destination) {
        retryAttempt += 1;
        const attempt = retryAttempt;
        const maxAttempts = 3; // attempts 1-3

        retryWeatherBtn.classList.add('retrying');
        retryBtnLabel.textContent = `Retrying... (${attempt}/${maxAttempts})`;
        retryWeatherBtn.disabled = true;

        // Delay before retry
        const delay = RETRY_DELAYS[attempt - 2] || 2000;
        await sleep(delay);

        try {
            const facts = await fetchTrailFactsRaw(destination, attempt);
            currentTrailFacts = facts;
            displayFactsForm(facts);
        } catch (err) {
            showInlineError(`Retry ${attempt} failed: ${err.message}`);
        } finally {
            retryWeatherBtn.classList.remove('retrying');
            retryWeatherBtn.disabled = false;
        }
    }

    retryWeatherBtn.addEventListener('click', () => {
        if (currentDestination) retryWithBackoff(currentDestination);
    });

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    // ─── Manual Weather Estimate Buttons ─────────────────────────────────────

    weatherEstimateBtns.querySelectorAll('.weather-est-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            // Deactivate others
            weatherEstimateBtns.querySelectorAll('.weather-est-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Fill the weather input
            factWeather.value = btn.dataset.value;
            weatherIsUserProvided = true;
            setWeatherSourceLabel('User Estimate');

            // Update facts state so agent picks up the user-provided flag
            if (currentTrailFacts) {
                currentTrailFacts.weather_status = 'user_provided';
                currentTrailFacts.weather_source = 'user_provided';
            }
        });
    });

    // ─── Trail Searching & Facts Retrieval ───────────────────────────────────

    searchForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const query = destinationInput.value.trim();
        if (!query) return;
        retryAttempt = 1;  // reset counter for a fresh search
        currentDestination = query;
        await fetchTrailFacts(query);
    });

    async function fetchTrailFacts(destinationName) {
        searchSpinner.classList.remove('hidden');
        searchBtn.disabled = true;
        ambiguityPanel.classList.add('hidden');

        try {
            const data = await fetchTrailFactsRaw(destinationName, 1);

            if (data.ambiguous) {
                showAmbiguity(data.suggestions);
            } else {
                currentTrailFacts = data;
                displayFactsForm(data);
            }
        } catch (error) {
            showInlineError(error.message);
        } finally {
            searchSpinner.classList.add('hidden');
            searchBtn.disabled = false;
        }
    }

    async function fetchTrailFactsRaw(destinationName, attempt) {
        const response = await fetch('/api/fetch-trail', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                destination: destinationName,
                api_key: getApiKey(),
                attempt
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to fetch trail details.');
        }

        return response.json();
    }

    function showAmbiguity(suggestions) {
        suggestionList.innerHTML = '';
        suggestions.forEach(suggestion => {
            const btn = document.createElement('button');
            btn.className = 'suggestion-btn';
            btn.textContent = suggestion;
            btn.type = 'button';
            btn.addEventListener('click', () => {
                destinationInput.value = suggestion;
                currentDestination = suggestion;
                retryAttempt = 1;
                fetchTrailFacts(suggestion);
            });
            suggestionList.appendChild(btn);
        });
        ambiguityPanel.classList.remove('hidden');
    }

    function showInlineError(message) {
        // Non-blocking: use the timeout banner as a generic error display
        weatherTimeoutBanner.classList.remove('hidden');
        weatherTimeoutMsg.textContent = message;
    }

    function displayFactsForm(facts) {
        // Populate editable fields
        factName.value      = facts.name || '';
        factDistance.value  = facts.distance_miles || '';
        factElevation.value = facts.elevation_gain_feet || '';
        factTrailhead.value = facts.trailhead_location || '';
        factWeather.value   = facts.weather_forecast || '';
        factSunrise.value   = facts.sunrise || '6:00 AM';
        factSunset.value    = facts.sunset  || '8:00 PM';
        factTerrain.value   = facts.terrain || '';

        // Source badge on header
        factsSourceBadge.textContent = facts.source || 'Fetched Facts';
        factsSourceBadge.className = 'badge';
        if (facts.source && facts.source.includes('Mock')) {
            factsSourceBadge.classList.add('badge-fetched');
        } else if (facts.source && facts.source.includes('AI')) {
            factsSourceBadge.classList.add('badge-inferred');
        } else if (!facts.found) {
            factsSourceBadge.classList.add('badge-user');
        } else {
            factsSourceBadge.classList.add('badge-fetched');
        }

        // Apply banners based on weather/trail status
        applyWeatherBanners(facts);

        // Retry button: reset label once shown
        retryBtnLabel.textContent = retryAttempt < 3 ? 'Retry' : 'Max retries reached';
        retryWeatherBtn.disabled  = retryAttempt >= 3;

        // Show Step 2 panel
        factsCard.classList.remove('hidden');
        factsCard.scrollIntoView({ behavior: 'smooth' });

        // Advance progress to step 2
        advanceProgress(2);
    }

    // ─── Packing Recommendation Generation ───────────────────────────────────

    generateForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const key = getApiKey();
        const hasServerKey = statusText.textContent.includes('(Env)');
        if (!key && !hasServerKey) {
            settingsModal.classList.remove('hidden');
            return;
        }

        // Gather edited facts — including weather source flag for backend agent
        const editedFacts = {
            name:                  factName.value,
            distance_miles:        parseFloat(factDistance.value) || 0,
            elevation_gain_feet:   parseFloat(factElevation.value) || 0,
            trailhead_location:    factTrailhead.value,
            weather_forecast:      factWeather.value,
            sunrise:               factSunrise.value,
            sunset:                factSunset.value,
            terrain:               factTerrain.value,
            // Carry through metadata for confidence calculations
            weather_status:        weatherIsUserProvided ? 'user_provided' : (currentTrailFacts?.weather_status || 'success'),
            weather_source:        weatherIsUserProvided ? 'user_provided' : (currentTrailFacts?.weather_source || 'fetched'),
            weather_timestamp:     currentTrailFacts?.weather_timestamp || '',
            source:                currentTrailFacts?.source || '',
            found:                 currentTrailFacts?.found ?? true,
            trail_status:          currentTrailFacts?.trail_status || ''
        };

        // Gather user preference answers
        const answers = {
            pace:                hikePace.value,
            start_time:          startTime.value,
            footwear_preference: footwearPref.value
        };

        generateSpinner.classList.remove('hidden');
        generateBtn.disabled = true;

        try {
            const response = await fetch('/api/generate-packing', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ facts: editedFacts, answers, api_key: key })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to generate recommendations.');
            }

            const recommendations = await response.json();
            displayRecommendations(recommendations, editedFacts, answers);

        } catch (error) {
            alert(error.message);
            console.error('Generate packing error:', error);
        } finally {
            generateSpinner.classList.add('hidden');
            generateBtn.disabled = false;
        }
    });

    // ─── Display Recommendations ──────────────────────────────────────────────

    function displayRecommendations(recs, facts, answers) {
        hikeSummaryText.textContent = `${facts.name} day hike — ${hikeDay.value}`;

        // ── Metrics
        valDuration.textContent = `${recs.duration_hours} hrs`;
        lblPace.textContent     = `${answers.pace.charAt(0).toUpperCase() + answers.pace.slice(1)} pace`;

        valWater.textContent    = `${recs.water_liters} L`;
        lblWaterRate.textContent = `For ${recs.duration_hours} hrs`;

        valSunset.textContent   = facts.sunset || '—';
        lblSunset.textContent   = 'Sunset';

        // ── Confidence Banner
        const isLowerConfidence = recs.confidence_level === 'Lower Confidence';
        if (isLowerConfidence) {
            confidenceBanner.classList.remove('hidden');
            const reasons = [];
            if (recs.weather_source === 'user_provided') reasons.push('weather was estimated');
            if (recs.trail_source === 'user_provided')   reasons.push('trail data was entered manually');
            if (facts.weather_status === 'stale')         reasons.push('weather data may be outdated');
            confidenceBannerText.innerHTML =
                `Some data was estimated (${reasons.join(', ')}). Items marked ` +
                `<span class="badge badge-low-confidence">Low Confidence</span> ` +
                `may vary from actual conditions.`;
        } else {
            confidenceBanner.classList.add('hidden');
        }

        // ── Checklists
        renderChecklist(listMust, recs.must_bring    || [], countMust);
        renderChecklist(listRec,  recs.recommended   || [], countRec);

        // Optional: merge AI items + any custom user items
        const optItems = recs.optional || [];
        customOptionalItems.forEach(name => {
            optItems.push({
                name,
                source: 'user_provided',
                confidence: 'high',
                explanation: 'Added by you.'
            });
        });
        renderChecklist(listOpt, optItems, countOpt, true);

        // Show the add-item row (only visible after recommendations are generated)
        addOptionalItemRow.classList.remove('hidden');

        // ── Safety Warnings Bar
        warningBar.classList.add('hidden');
        if (recs.safety_inferences && recs.safety_inferences.length > 0) {
            warningText.innerHTML = recs.safety_inferences
                .map(inf => `<div>• ${inf}</div>`).join('');
            warningBar.classList.remove('hidden');
        }

        // ── Transparency Explanations
        reportDuration.textContent = recs.duration_explanation ||
            'Hike duration calculated using trail distance and pace, with extra time for steep elevation gain.';
        reportWater.textContent    = recs.water_explanation ||
            'Water calculation baseline is 0.5L per hour, increased dynamically for warm weather and elevation.';

        const allItems = [...(recs.must_bring || []), ...(recs.recommended || []), ...(recs.optional || [])];
        const headlampItem = allItems.find(item => item.name.toLowerCase().includes('headlamp'));
        reportSafety.textContent = headlampItem
            ? headlampItem.explanation
            : `Estimates show you will complete the hike before sunset. A headlamp remains optional as an emergency item.`;

        // ── Reveal card
        recommendationPlaceholder.classList.add('hidden');
        recommendationCard.classList.remove('hidden');
        recommendationCard.scrollIntoView({ behavior: 'smooth' });

        // Advance progress to step 3
        advanceProgress(3);
    }

    // ─── Render Checklist with Source + Confidence Badges ────────────────────

    function renderChecklist(container, items, countBadge, isOptional = false) {
        container.innerHTML = '';
        countBadge.textContent = items.length;

        if (items.length === 0) {
            container.innerHTML = '<div class="item-explanation">No items in this category.</div>';
            return;
        }

        items.forEach((item, index) => {
            const isLowConfidence = item.confidence === 'low';

            const itemDiv  = document.createElement('div');
            itemDiv.className = 'checklist-item' + (isLowConfidence ? ' low-confidence' : '');

            // Checkbox
            const checkbox = document.createElement('input');
            checkbox.type  = 'checkbox';
            checkbox.className = 'item-checkbox';
            checkbox.id    = `chk-${container.id}-${index}`;

            // Content wrapper
            const contentDiv = document.createElement('div');
            contentDiv.className = 'item-content';

            // Name row (name + source tag + confidence tag)
            const nameRow = document.createElement('div');
            nameRow.style.cssText = 'display: flex; align-items: center; flex-wrap: wrap; gap: 0.25rem;';

            const nameSpan = document.createElement('span');
            nameSpan.className = 'item-name';
            nameSpan.textContent = item.name;
            nameRow.appendChild(nameSpan);

            // Source tag
            if (item.source) {
                const sourceTag = document.createElement('span');
                sourceTag.className = 'item-source-tag';
                switch (item.source) {
                    case 'fetched':
                        sourceTag.classList.add('badge-fetched');
                        sourceTag.textContent = 'Fact';
                        break;
                    case 'user_provided':
                        sourceTag.classList.add('badge-user');
                        sourceTag.textContent = 'Answer';
                        break;
                    default: // inferred
                        sourceTag.classList.add('badge-inferred');
                        sourceTag.textContent = 'Advice';
                }
                nameRow.appendChild(sourceTag);
            }

            // Low-confidence tag
            if (isLowConfidence) {
                const confTag = document.createElement('span');
                confTag.className = 'item-confidence-tag';
                confTag.textContent = 'Low Confidence';
                nameRow.appendChild(confTag);
            }

            contentDiv.appendChild(nameRow);

            // Explanation
            const explSpan = document.createElement('span');
            explSpan.className = 'item-explanation';
            explSpan.textContent = item.explanation;
            contentDiv.appendChild(explSpan);

            itemDiv.appendChild(checkbox);
            itemDiv.appendChild(contentDiv);
            container.appendChild(itemDiv);

            // Toggle strike-through on check
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) {
                    nameSpan.style.textDecoration = 'line-through';
                    nameSpan.style.opacity = '0.4';
                    explSpan.style.opacity = '0.25';
                } else {
                    nameSpan.style.textDecoration = 'none';
                    nameSpan.style.opacity = '1';
                    explSpan.style.opacity = '0.7';
                }
            });
        });
    }

    // ─── Add Custom Optional Item ─────────────────────────────────────────────

    function addCustomItemToList(name) {
        name = name.trim();
        if (!name) return;

        // Store in state
        if (!customOptionalItems.includes(name)) {
            customOptionalItems.push(name);
        }

        // Render a new item row directly
        const index = listOpt.querySelectorAll('.checklist-item').length;

        const itemDiv  = document.createElement('div');
        itemDiv.className = 'checklist-item custom-item';

        const checkbox = document.createElement('input');
        checkbox.type  = 'checkbox';
        checkbox.className = 'item-checkbox';
        checkbox.id    = `chk-opt-custom-${index}`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'item-content';

        const nameRow = document.createElement('div');
        nameRow.style.cssText = 'display: flex; align-items: center; flex-wrap: wrap; gap: 0.25rem;';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'item-name';
        nameSpan.textContent = name;
        nameRow.appendChild(nameSpan);

        const sourceTag = document.createElement('span');
        sourceTag.className = 'item-source-tag badge-user';
        sourceTag.textContent = 'My item';
        nameRow.appendChild(sourceTag);

        contentDiv.appendChild(nameRow);

        const explSpan = document.createElement('span');
        explSpan.className = 'item-explanation';
        explSpan.textContent = 'Added by you.';
        contentDiv.appendChild(explSpan);

        itemDiv.appendChild(checkbox);
        itemDiv.appendChild(contentDiv);
        listOpt.appendChild(itemDiv);

        // Update count badge
        countOpt.textContent = parseInt(countOpt.textContent || '0') + 1;

        checkbox.addEventListener('change', () => {
            nameSpan.style.textDecoration = checkbox.checked ? 'line-through' : 'none';
            nameSpan.style.opacity        = checkbox.checked ? '0.4' : '1';
            explSpan.style.opacity        = checkbox.checked ? '0.25' : '0.7';
        });

        customItemInput.value = '';
        customItemInput.focus();
    }

    addCustomItemBtn.addEventListener('click', () => addCustomItemToList(customItemInput.value));

    customItemInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addCustomItemToList(customItemInput.value);
        }
    });

    // Hide add-item-row initially (shown only after packing list is generated)
    addOptionalItemRow.classList.add('hidden');

    // ─── Reset ────────────────────────────────────────────────────────────────

    resetBtn.addEventListener('click', () => {
        factsCard.classList.add('hidden');
        recommendationCard.classList.add('hidden');
        recommendationPlaceholder.classList.remove('hidden');
        confidenceBanner.classList.add('hidden');
        addOptionalItemRow.classList.add('hidden');

        destinationInput.value = '';
        ambiguityPanel.classList.add('hidden');
        suggestionList.innerHTML = '';

        hideAllBanners();

        currentTrailFacts     = null;
        weatherStatus         = 'success';
        weatherIsUserProvided = false;
        retryAttempt          = 1;
        currentDestination    = '';
        customOptionalItems   = [];

        // Reset progress tracker
        advanceProgress(1);

        searchForm.scrollIntoView({ behavior: 'smooth' });
    });

});
