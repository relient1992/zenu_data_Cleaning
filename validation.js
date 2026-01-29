    // --- GLOBAL STATE ---
    let workbook; // For holding the loaded XLSX file
    let uploadedHeaders = [];
    let uploadedData = [];
    let invalidEntries = []; // New global variable to store invalid data

    // --- TARGET HEADERS & CONFIG ---
    const targetHeaders = {
        contact: [
            "contact_identifier", "zenu_contact_id", "contact_title", "contact_first_name", "contact_surname",
            "contact_is_company", "contact_company_name", "contact_name_on_letters", "contact_legal_name",
            "contact_company_position", "contact_type", "contact_tags", "contact_subscriptions",
            "contact_mailing_address_line1", "contact_mailing_address_line2", "contact_mailing_address_suburb",
            "contact_mailing_address_town_city", "contact_mailing_address_state", "contact_mailing_address_country",
            "contact_mailing_address_postcode", "contact_email_address", "contact_mobile", "contact_phone_work",
            "contact_phone_home", "contact_fax", "contact_allow_contact_via_text", "contact_allow_contact_via_email",
            "contact_allow_contact_via_letter", "contact_allow_contact_via_phone", "contact_unsubscribe_from_marketing",
            "contact_childrens_names", "contact_enquiry_source", "contact_rating", "contact_partner_identifier",
            "contact_partnership_id", "contact_partnership_type", "contact_last_contact_date", "contact_date_created",
            "contact_date_modified", "contact_criteria_sale_method", "contact_criteria_category",
            "contact_criteria_property_type", "contact_criteria_title_type", "contact_criteria_price_from",
            "contact_criteria_price_to", "contact_criteria_bedrooms", "contact_criteria_bathrooms",
            "contact_criteria_carspaces", "contact_criteria_land_from", "contact_criteria_land_to",
            "contact_criteria_land_unit", "contact_criteria_living_area_from", "contact_criteria_living_area_to",
            "contact_criteria_living_area_unit", "contact_criteria_suburbs", "contact_team_member_1",
            "contact_team_member_2", "contact_sale_type", "zenu_solicitor_id", "zenu_company_id", "contact_notes",
            "contact_note_created_date", "contact_note_team_member"
        ],
        property: [
            "property_identifier", "zenu_property_id", "property_unit_number", "property_street_number",
            "property_street_name", "property_suburb", "property_postcode", "property_state",
            "property_country", "property_full_address", "property_building_name", "property_sale_method",
            "property_category", "property_type", "property_bedrooms", "property_bathrooms",
            "property_toilets", "property_total_rooms", "property_garages", "property_carports",
            "property_open_parking_spaces", "property_living_area_m2", "property_land_size_m2",
            "property_modified_date", "property_appraisal_date", "property_timeline_status",
            "property_team_member_1", "property_team_member_2", "property_is_occupied_by_owner",
            "property_appraisal_source", "property_is_selling_within_7_days", "property_search_price",
            "property_vendor_price", "property_estimated_commission", "property_pipeline_rating",
            "property_contract_date", "property_unconditional_date", "property_settlement_date",
            "property_sold_price", "property_sale_team_member", "property_sale_team_member_role",
            "property_sale_team_member_recognition_percentage", "property_sale_team_member_recognition_amount",
            "property_sale_gross_commission", "property_year_built", "property_council_name",
            "property_council_zoning", "property_lot_number", "property_title_number",
            "property_rent_per_week", "property_last_listed_price", "property_current_price",
            "property_last_listed_date", "property_last_contract_date", "property_last_sold_date",
            "property_last_sold_price", "property_last_listed_by_agency", "property_last_listed_by_agent",
            "property_last_leased_agency", "property_last_rent_pw", "property_notes",
            "property_note_created_date", "property_note_team_member"
        ]
    };
    const phoneFields = ["contact_mobile", "contact_phone_work", "contact_phone_home", "contact_fax"];

    const trimFields = [
        "contact_first_name", 
        "contact_surname",
        "contact_company_name",
        "contact_mailing_address_line1",
        "contact_mailing_address_suburb",
        "contact_email_address",
        "property_unit_number",
        "property_street_number",
        "property_street_name",
        "property_suburb",
        "property_postcode"
    ];

    // --- DOM ELEMENT REFERENCES ---
    const dataFileInput = document.getElementById('dataFile');
    const sheetSelectorSection = document.getElementById('sheetSelectorSection');
    const sheetSelector = document.getElementById('sheetSelector');
    const dataTypeSelector = document.getElementById('dataTypeSelector');
    const mappingContainer = document.getElementById('mappingContainer');
    const mappingTableBody = document.querySelector('#mappingTable tbody');
    const addIdButton = document.getElementById('addIdButton');
    const processButton = document.getElementById('processButton');
    const validationErrorsSection = document.getElementById('validationErrorsSection');
    const errorListDiv = document.getElementById('errorList');
    const saveErrorsButton = document.getElementById('saveErrorsButton');
    const downloadErrorsButton = document.getElementById('downloadErrorsButton');
    const transferButton = document.getElementById('transferButton');
    const cleanedDataFileInput = document.getElementById('cleanedDataFile');
    const templateFileInput = document.getElementById('templateFile');

    // --- EVENT LISTENERS ---
    dataFileInput.addEventListener('change', handleFileSelect);
    sheetSelector.addEventListener('change', handleSheetSelect);
    dataTypeSelector.addEventListener('change', () => {
        if (uploadedHeaders.length > 0) {
            populateMappingTable(uploadedHeaders);
        }
    });
    addIdButton.addEventListener('click', addIdentifierField);
    processButton.addEventListener('click', startProcessing);
    saveErrorsButton.addEventListener('click', saveEditsAndRevalidate);
    downloadErrorsButton.addEventListener('click', downloadErrorFile);
    transferButton.addEventListener('click', handleTemplateFill);
    document.getElementById('forceSaveButton').addEventListener('click', forceSaveWithRemarks);

    // --- CORE LOGIC ---

    function togglePhase(phase) {
        document.getElementById('phase1Container').style.display = (phase === 'phase1') ? 'block' : 'none';
        document.getElementById('phase2Container').style.display = (phase === 'phase2') ? 'block' : 'none';
    }

    function handleFileSelect(event) {
        const file = event.target.files[0];
        if (!file) return;

        mappingContainer.classList.add('hidden');
        sheetSelectorSection.classList.add('hidden');

        const reader = new FileReader();
        const isXLSX = file.name.endsWith('.xlsx');

        reader.onload = e => {
            if (isXLSX) {
                workbook = XLSX.read(e.target.result, { type: 'binary' });
                sheetSelector.innerHTML = '';
                workbook.SheetNames.forEach(name => {
                    const option = document.createElement('option');
                    option.value = name;
                    option.textContent = name;
                    sheetSelector.appendChild(option);
                });
                sheetSelectorSection.classList.remove('hidden');
                handleSheetSelect(); // Process the first sheet by default
            } else { // CSV
                const { headers, data } = parseCSV(e.target.result);
                processFileData(headers, data);
            }
        };

        if (isXLSX) {
            reader.readAsBinaryString(file);
        } else {
            reader.readAsText(file);
        }
    }

    function handleSheetSelect() {
        const sheetName = sheetSelector.value;
        const worksheet = workbook.Sheets[sheetName];
        const rawData = XLSX.utils.sheet_to_json(worksheet, { header: 1 });
        if (rawData.length > 0) {
            const headers = rawData[0].map(String);
            const data = rawData.slice(1);
            processFileData(headers, data);
        } else {
            alert('Selected sheet is empty.');
        }
    }

    function processFileData(headers, data) {
        uploadedHeaders = headers;
        uploadedData = data;
        validationErrorsSection.classList.add('hidden');
        populateMappingTable(headers);
        mappingContainer.classList.remove('hidden');
    }

    function parseCSV(text) {
        const lines = text.trim().split(/\r\n|\n/);
        const csvRowRegex = /("([^"]|"")*"|[^,]*)(,|$)/g;

        const parseRow = rowString => {
            const row = [];
            let match;
            csvRowRegex.lastIndex = 0; 
            while (match = csvRowRegex.exec(rowString)) {
                let value = match[1];
                if (value.startsWith('"') && value.endsWith('"')) {
                    value = value.slice(1, -1).replace(/""/g, '"');
                }
                row.push(value);
                if (match[3] === '') break;
            }
            return row;
        };

        const headers = lines[0] ? parseRow(lines[0]) : [];
        const data = lines.length > 1 ? lines.slice(1).map(parseRow) : [];

        return { headers, data };
    }

    function populateMappingTable(headers) {
        mappingTableBody.innerHTML = '';
        const selectedDataType = dataTypeSelector.value;
        const availableTargetHeaders = targetHeaders[selectedDataType];

        headers.forEach(header => {
            if (header) {
                const row = document.createElement('tr');
                row.innerHTML = `<td>${header}</td><td><select data-original-header="${header}"></select></td>`;
                const select = row.querySelector('select');
                let options = `<option value="ignore">-- Ignore this field --</option>`;
                
                // Add the special "Split Address" option only for Property
                if (selectedDataType === 'property') {
                     options += `<option value="split_address">-- Split Address --</option>`;
                }
                
                availableTargetHeaders.forEach(destHeader => {
                    options += `<option value="${destHeader}">${destHeader}</option>`;
                });
                select.innerHTML = options;
                mappingTableBody.appendChild(row);
            }
        });
    }

    function addIdentifierField() {
        const selectedDataType = dataTypeSelector.value;
        const identifierField = (selectedDataType === 'contact') ? 'contact_identifier' : 'property_identifier';

        if (document.querySelector(`[data-original-header="__GENERATE_ID__"][data-target-field="${identifierField}"]`)) {
            alert(`A 'Generate ID' field for ${selectedDataType} has already been added.`);
            return;
        }
        
        const row = document.createElement('tr');
        row.innerHTML = `<td><b>(New Field) Generate ID</b></td><td><select data-original-header="__GENERATE_ID__" data-target-field="${identifierField}"></select></td>`;
        const select = row.querySelector('select');
        select.innerHTML = `<option value="${identifierField}">${identifierField}</option>`;
        select.disabled = true;
        mappingTableBody.prepend(row);
    }

    function getMappings() {
        const mappingSelects = mappingTableBody.querySelectorAll('select');
        const mappings = {};
        mappingSelects.forEach(select => {
            const originalHeader = select.getAttribute('data-original-header');
            const selectedValue = select.value;

            if (selectedValue === 'ignore') return;

            if (originalHeader === '__GENERATE_ID__') {
                const targetField = select.getAttribute('data-target-field');
                mappings[targetField] = { type: 'generate' };
            } else if (selectedValue === 'split_address') {
                mappings['property_address_split'] = { type: 'addressSplit', original: originalHeader };
            } else {
                mappings[selectedValue] = { type: 'map', original: originalHeader };
            }
        });
        return mappings;
    }

    function startProcessing() {
        validationErrorsSection.classList.add('hidden');
        const mappings = getMappings();
        invalidEntries = [];

        uploadedData.forEach((row, rowIndex) => {
            for (const destHeader in mappings) {
                const mappingInfo = mappings[destHeader];
                if (mappingInfo.type !== 'map') continue;

                const colIndex = uploadedHeaders.indexOf(mappingInfo.original);
                if (colIndex === -1) continue;

                let value = row[colIndex] || '';

                if (trimFields.includes(destHeader)) {
                    value = String(value).trim();
                }

                let isValid = true;
                let remark = '';

                // --- UPDATED EMAIL VALIDATION LOGIC ---
                if (destHeader === 'contact_email_address' && value) {
                    // 1. CLEAN IT FIRST (Remove mailto, spaces, brackets)
                    const cleanedValue = cleanEmailInput(value);

                    // 2. SPLIT by comma or semicolon
                    const emailParts = cleanedValue.split(/[,;]/).map(e => e.trim()).filter(e => e !== '');
                    
                    // 3. CHECK VALIDITY
                    const hasInvalidEmail = emailParts.some(part => !isValidEmail(part));

                    if (hasInvalidEmail) {
                        isValid = false;
                        remark = `Invalid email format found in: '${row[colIndex]}'`;
                    }
                } 
                // --- EXISTING PHONE LOGIC ---
                else if (phoneFields.includes(destHeader) && value && !isValidAustralianPhone(value)) {
                    isValid = false;
                    remark = `Invalid phone number: '${row[colIndex]}'`;
                }

                if (!isValid) {
                    invalidEntries.push({ rowIndex, colIndex, value: row[colIndex], header: mappingInfo.original, remark });
                }
            }
        });

        if (invalidEntries.length > 0) {
            displayEditableErrors(invalidEntries);
            document.getElementById('forceSaveButton').style.display = 'inline-block';
        } else {
            document.getElementById('forceSaveButton').style.display = 'none';
            buildAndDownloadFinalFile(mappings, false);
        }
    }

    function displayEditableErrors(errors) {
        errorListDiv.innerHTML = '';
        errors.forEach((err, index) => {
            const itemDiv = document.createElement('div');
            itemDiv.className = 'error-item';
            itemDiv.innerHTML = `
                <span class="error-item-label">Row ${err.rowIndex + 2} (${err.header}):</span>
                <div class="error-item-input">
                    <input type="text" value="${String(err.value || '').replace(/"/g, '&quot;')}" data-row-index="${err.rowIndex}" data-col-index="${err.colIndex}">
                </div>`;
            errorListDiv.appendChild(itemDiv);
        });
        validationErrorsSection.classList.remove('hidden');
    }

    function forceSaveWithRemarks() {
        const mappings = getMappings();
        buildAndDownloadFinalFile(mappings, true, invalidEntries);
    }

    function saveEditsAndRevalidate() {
        const errorInputs = errorListDiv.querySelectorAll('input[type="text"]');
        errorInputs.forEach(input => {
            const rowIndex = parseInt(input.dataset.rowIndex, 10);
            const colIndex = parseInt(input.dataset.colIndex, 10);
            uploadedData[rowIndex][colIndex] = input.value;
        });
        alert(`${errorInputs.length} change(s) saved. Re-validating...`);
        startProcessing();
    }

    function downloadErrorFile() {
        const invalidRows = new Set(Array.from(errorListDiv.querySelectorAll('input[type="text"]')).map(input => parseInt(input.dataset.rowIndex, 10)));
        const content = [
            uploadedHeaders.join(','),
            ...Array.from(invalidRows).map(rowIndex => uploadedData[rowIndex].join(','))
        ].join('\n');
        downloadCSV(content, 'invalid_rows.csv');
    }

    // --- Address Splitting Function ---
    function splitAddressField(address) {
        const result = { unit: '', streetNumber: '', streetName: '' };
        if (!address) return result;

        const trimmedAddress = address.trim();
        
        // Regex 1: Handles "Unit/Number" and "Unit, Number" formats
        const unitStreetRegex1 = /^(?:unit|apt|suite|flat)\s*(\S+)\s*,?\s*(\S+)\s+(.*)$/i;
        // Regex 2: Handles "Number/Number" format
        const unitStreetRegex2 = /^(\S+)\s*\/\s*(\S+)\s+(.*)$/;
        // Regex 3: Handles simple "Number StreetName" format
        const simpleStreetRegex = /^(\S+)\s+(.*)$/;

        let match;
        if (match = trimmedAddress.match(unitStreetRegex1)) {
            result.unit = match[1].replace(/,/g, '');
            result.streetNumber = match[2];
            result.streetName = match[3];
        } else if (match = trimmedAddress.match(unitStreetRegex2)) {
            result.unit = match[1];
            result.streetNumber = match[2];
            result.streetName = match[3];
        } else if (match = trimmedAddress.match(simpleStreetRegex)) {
            result.streetNumber = match[1];
            result.streetName = match[2];
        }

        // Clean up empty strings
        for (const key in result) {
            result[key] = result[key].trim();
        }
        
        return result;
    }

function buildAndDownloadFinalFile(mappings, forceSave = false, errors = []) {
        let finalHeaders = Object.keys(mappings);
        const errorMap = new Map();
        
        errors.forEach(err => {
            if (!errorMap.has(err.rowIndex)) {
                errorMap.set(err.rowIndex, {});
            }
            errorMap.get(err.rowIndex)[err.header] = err.remark;
        });

        let finalHeadersSet = new Set(finalHeaders);
        let newCsvRows = [];

        // --- HELPER: NAME SPLITTING LOGIC (Step 4) ---
        function detectAndProcessNames(rowMap) {
            // 1. GARBAGE CLEANING (N/A, Unknown, symbols)
            const garbageValues = ["N/A", "NA", "UNKNOWN", ".", "?", ","];
            const cleanField = (val) => {
                if (!val) return '';
                const str = String(val).trim();
                return garbageValues.includes(str.toUpperCase()) ? '' : str;
            };

            let fName = cleanField(rowMap['contact_first_name']);
            let sName = cleanField(rowMap['contact_surname']);
            let rawMappedTitle = cleanField(rowMap['contact_title']);
            let company = cleanField(rowMap['contact_company_name']);

            // --- NEW LOGIC: IF NAMES ARE BLANK, CLEAR TITLE ---
            if (!fName && !sName) {
                rawMappedTitle = '';
                // Ensure rowMap is explicitly cleared so garbage doesn't persist
                rowMap['contact_first_name'] = '';
                rowMap['contact_surname'] = '';
                rowMap['contact_title'] = '';
            }

            // 2. COMPANY KEYWORD CHECK
            const companyRegex = /\b(PTY|LTD|Corporation|Services|Holding|Trust|Trustee|pty ltd|limited|accounts|THE)\b/i;
            
            if (companyRegex.test(fName) || companyRegex.test(sName)) {
                if (!company) {
                    const fullString = (fName + " " + sName).trim();
                    rowMap['contact_company_name'] = fullString;
                    if (!finalHeadersSet.has('contact_company_name')) {
                        finalHeadersSet.add('contact_company_name');
                        finalHeaders.push('contact_company_name');
                    }
                }
                rowMap['contact_first_name'] = '';
                rowMap['contact_surname'] = '';
                rowMap['contact_title'] = '';
                return; 
            }

            // 3. TITLE SPLITTING
            let splitTitles = [];
            if (rawMappedTitle) {
                const splitRegex = /\s*(?:and|&|\/|,)\s*/gi;
                splitTitles = rawMappedTitle.split(splitRegex).map(t => t.trim()).filter(t => t);
            }

            const allowedTitles = ["MR", "MRS", "MS", "MISS", "DR"];
            const isValidTitle = (t) => {
                const cleanT = String(t || '').replace(/\./g, '').trim().toUpperCase();
                return allowedTitles.includes(cleanT);
            };

            // 4. NAME NORMALIZE
            fName = fName.replace(/\s?\(.*?\)/g, '');
            sName = sName.replace(/\s?\(.*?\)/g, '');
            const normalize = (str) => str.replace(/\s+(and|&)\s+|,\s*|\s*\/\s*|\s+;\s+|\s+\+\s+/gi, '&');
            fName = normalize(fName);
            sName = normalize(sName);

            const lastNamePrefixes = ["Van De ", "Van ", "De Los ", "Delos ", "Dela ", "De La ", "De "];

            // 5. JOINT NAMES
            if (fName.trim().endsWith('&')) {
                fName = fName + " " + sName;
                sName = ""; 
            }
            if (sName.trim().startsWith('&')) {
                sName = sName.trim().substring(1).trim();
                const firstSpace = sName.indexOf(' ');
                if (firstSpace !== -1) {
                    const partToMove = sName.substring(0, firstSpace);
                    fName = fName + "&" + partToMove;
                    sName = sName.substring(firstSpace + 1);
                } else {
                    fName = fName + "&" + sName;
                    sName = "";
                }
            }

            // 6. SPLIT INTO LISTS
            let firstNamesList = fName.split('&').map(s => s.trim()).filter(s => s);
            let lastNamesList = sName.split('&').map(s => s.trim()).filter(s => s);

            // 7. DISTRIBUTE
            let count = Math.max(firstNamesList.length, lastNamesList.length);
            
            for (let i = 0; i < count; i++) {
                let currentFirst = firstNamesList[i] || '';
                let currentLast = '';

                if (lastNamesList.length > i) {
                    currentLast = lastNamesList[i];
                } else if (lastNamesList.length > 0) {
                    currentLast = lastNamesList[lastNamesList.length - 1];
                }

                let finalPersonTitle = '';
                const titleRegex = /^(Mrs|Mr|Ms|Miss|Dr)\.?\s+/i;
                const titleMatch = currentFirst.match(titleRegex);
                
                if (titleMatch) {
                    let extracted = titleMatch[1];
                    if (isValidTitle(extracted)) {
                        finalPersonTitle = extracted;
                    }
                    currentFirst = currentFirst.replace(titleRegex, '').trim();
                } else if (splitTitles.length > i) {
                    let candidate = splitTitles[i];
                    if (isValidTitle(candidate)) {
                        finalPersonTitle = candidate;
                    }
                } else if (i === 0 && splitTitles.length > 0) {
                     let candidate = splitTitles[0];
                     if (isValidTitle(candidate)) {
                         finalPersonTitle = candidate;
                     }
                }

                for (let prefix of lastNamePrefixes) {
                    if (currentLast.toUpperCase().startsWith(prefix.toUpperCase())) {
                        break; 
                    }
                }

                let suffix = (i === 0) ? '' : `_${i + 1}`;
                let tHeader = `contact_title${suffix}`;
                let fHeader = `contact_first_name${suffix}`;
                let sHeader = `contact_surname${suffix}`;

                [tHeader, fHeader, sHeader].forEach(h => {
                    if (!finalHeadersSet.has(h)) {
                        finalHeadersSet.add(h);
                        finalHeaders.push(h);
                    }
                });

                rowMap[tHeader] = finalPersonTitle;
                rowMap[fHeader] = currentFirst;
                rowMap[sHeader] = currentLast;
            }
        }

        // --- MAIN ROW PROCESSING ---
        uploadedData.forEach((row, rowIndex) => {
            const rowMap = {};
            const rowErrors = errorMap.get(rowIndex) || {};
            let remarksList = []; 
            
            function addRemark(text) {
                remarksList.push(text);
            }

            function ensureHeader(h) {
                if (!finalHeadersSet.has(h)) {
                    finalHeadersSet.add(h);
                    finalHeaders.push(h);
                }
            }

            // --- STEP 1: BASIC MAPPING ---
            finalHeaders.forEach(newHeader => {
                if (newHeader === 'Remarks') return;
                const mappingInfo = mappings[newHeader];
                let value = '';

                if (mappingInfo && mappingInfo.type === 'generate') {
                    value = (newHeader === 'contact_identifier') ? generateUniqueId() : generateUniqueId();
                } else if (mappingInfo && mappingInfo.type === 'map') {
                    const originalHeader = mappingInfo.original;
                    const originalIndex = uploadedHeaders.indexOf(originalHeader);
                    if (originalIndex !== -1) {
                        const originalValue = row[originalIndex] || '';
                        
                        const isEmailField = (newHeader === 'contact_email_address');
                        
                        if (forceSave && rowErrors[originalHeader] && !isEmailField) {
                            value = '';
                            addRemark(`${originalHeader}: ${rowErrors[originalHeader]}`);
                        } else {
                            value = originalValue;
                            if (trimFields.includes(newHeader)) {
                                value = String(value).trim();
                            }
                            if (phoneFields.includes(newHeader) && value) {
                                value = cleanAndFormatPhoneNumber(value);
                            }
                        }
                    }
                }
                rowMap[newHeader] = value;
            });

            // Address Split
            const addressSplitMapping = mappings['property_address_split'];
            if (addressSplitMapping) {
                const originalHeader = addressSplitMapping.original;
                const originalIndex = uploadedHeaders.indexOf(originalHeader);
                if (originalIndex !== -1) {
                    const fullAddress = row[originalIndex] || '';
                    const split = splitAddressField(fullAddress);
                    ['property_unit_number', 'property_street_number', 'property_street_name'].forEach(h => ensureHeader(h));
                    if(split.unit) rowMap['property_unit_number'] = split.unit;
                    if(split.streetNumber) rowMap['property_street_number'] = split.streetNumber;
                    if(split.streetName) rowMap['property_street_name'] = split.streetName;
                }
            }

            // --- STEP 1.5: HARVEST MISPLACED EMAILS ---
            const emailSourceFields = [
                { header: 'contact_first_name', label: 'Name Email' },
                { header: 'contact_surname', label: 'Name Email' },
                { header: 'contact_mobile', label: 'Mobile Email' },
                { header: 'contact_phone_home', label: 'Home Email' },
                { header: 'contact_phone_work', label: 'Work Email' },
                { header: 'contact_fax', label: 'Fax Email' },
                { header: 'contact_company_name', label: 'Company Email' }
            ];

            emailSourceFields.forEach(source => {
                let val = rowMap[source.header] || '';
                if (!val || val.indexOf('@') === -1) return; 

                // 1. SMART CLEAN: Remove spaces around @
                val = val.replace(/\s*@\s*/g, '@');

                // 2. EXTRACT EMAILS
                const emailRegex = /([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/gi;
                let matches = val.match(emailRegex);

                if (matches && matches.length > 0) {
                    matches.forEach(foundEmail => {
                        if (isValidEmail(foundEmail)) {
                            // Check against the current main email field
                            const currentMainRaw = rowMap['contact_email_address'] || '';
                            const existingEmails = cleanEmailInput(currentMainRaw)
                                .toLowerCase()
                                .split(/[,;]/)
                                .map(e => e.trim());

                            if (!currentMainRaw) {
                                // CASE A: Main email is empty -> Move it there
                                ensureHeader('contact_email_address');
                                rowMap['contact_email_address'] = foundEmail;
                            } else if (existingEmails.includes(foundEmail.toLowerCase())) {
                                // CASE B: Duplicate -> Just remove from source
                            } else {
                                // CASE C: New/Different -> Add to remarks
                                addRemark(`${source.label}: ${foundEmail}`);
                            }
                            
                            val = val.replace(foundEmail, '').trim();
                        }
                    });
                    
                    // Clean up source field
                    val = val.replace(/\s+/g, ' ').replace(/^,|,$/g, '').trim();
                    rowMap[source.header] = val;
                }
            });

            // --- STEP 2: EMAIL CLEANING, VALIDATION & SPLITTING ---
            if (rowMap['contact_email_address']) {
                const rawEmailString = rowMap['contact_email_address'];
                
                // 1. CLEAN FIRST
                const cleanedEmailString = cleanEmailInput(rawEmailString);

                // 2. SPLIT
                const emailParts = cleanedEmailString.split(/[,;]/).map(e => e.trim()).filter(e => e !== '');
                
                rowMap['contact_email_address'] = '';
                
                let firstValidFound = false;

                emailParts.forEach(emailPart => {
                    if (isValidEmail(emailPart)) {
                        if (!firstValidFound) {
                            rowMap['contact_email_address'] = emailPart;
                            firstValidFound = true;
                        } else {
                            ensureHeader('Extra email address');
                            if (!rowMap['Extra email address']) {
                                rowMap['Extra email address'] = emailPart;
                            } else {
                                rowMap['Extra email address'] += `, ${emailPart}`;
                            }
                        }
                    } else {
                        addRemark(`Invalid Email: ${emailPart}`);
                    }
                });
            }

            // --- STEP 3: PHONE LOGIC ---
            const phoneConfig = [
                { header: 'contact_mobile', label: 'Mobile' },
                { header: 'contact_phone_home', label: 'Home' },
                { header: 'contact_phone_work', label: 'Work' },
                { header: 'contact_fax', label: 'Fax' }
            ];

            let candidates = [];
            
            phoneConfig.forEach(config => {
                let rawValue = rowMap[config.header] || '';
                rowMap[config.header] = ''; 

                if (!rawValue) return;

                let formatted = cleanAndFormatPhoneNumber(rawValue);
                let type = 'invalid';
                
                if (isMobile(formatted)) type = 'mobile';
                else if (isLandline(formatted)) type = 'landline';

                if (type === 'invalid') {
                    addRemark(`Invalid ${config.label}: ${rawValue}`);
                } else {
                    candidates.push({
                        sourceHeader: config.header,
                        sourceLabel: config.label,
                        value: formatted,
                        type: type,
                        used: false
                    });
                }
            });

            const mobileSlotCandidate = candidates.find(c => c.type === 'mobile' && c.sourceHeader === 'contact_mobile') 
                                     || candidates.find(c => c.type === 'mobile' && c.sourceHeader === 'contact_phone_home')
                                     || candidates.find(c => c.type === 'mobile' && c.sourceHeader === 'contact_phone_work')
                                     || candidates.find(c => c.type === 'mobile' && c.sourceHeader === 'contact_fax');

            if (mobileSlotCandidate) {
                rowMap['contact_mobile'] = mobileSlotCandidate.value;
                mobileSlotCandidate.used = true;
            }

            candidates.forEach(c => {
                if (!c.used && c.type === 'landline') {
                    if (['contact_phone_home', 'contact_phone_work', 'contact_fax'].includes(c.sourceHeader)) {
                        ensureHeader(c.sourceHeader);
                        rowMap[c.sourceHeader] = c.value;
                        c.used = true;
                    }
                }
            });

            candidates.forEach(c => {
                if (!c.used && c.type === 'landline' && c.sourceHeader === 'contact_mobile') {
                    if (!rowMap['contact_phone_home']) {
                        ensureHeader('contact_phone_home');
                        rowMap['contact_phone_home'] = c.value;
                        c.used = true;
                    } else if (!rowMap['contact_phone_work']) {
                        ensureHeader('contact_phone_work');
                        rowMap['contact_phone_work'] = c.value;
                        c.used = true;
                    }
                }
            });

            candidates.forEach(c => {
                if (!c.used) {
                    let label = '';
                    if (c.type === 'mobile') {
                        label = `Extra mobile from ${c.sourceLabel}`;
                    } else if (c.type === 'landline') {
                        if (c.sourceHeader === 'contact_mobile') label = `Extra Landline from Mobile`;
                        else label = `Extra Landline from ${c.sourceLabel}`;
                    }

                    if (label) {
                        const additionalHeader = 'Contact_additional_phone';
                        ensureHeader(additionalHeader);
                        const concatText = `${label}: ${c.value}`;
                        
                        if (!rowMap[additionalHeader]) {
                            rowMap[additionalHeader] = concatText;
                        } else {
                            rowMap[additionalHeader] += ` | ${concatText}`;
                        }
                        ensureHeader(label);
                        rowMap[label] = c.value;
                    }
                }
            });

            // --- STEP 4: CONTACT NAME SPLITTING ---
            detectAndProcessNames(rowMap);

            // --- STEP 5: FINALIZE ROW ---
            if (remarksList.length > 0) {
                ensureHeader('Remarks');
                rowMap['Remarks'] = remarksList.join('; ');
            }

            const newRow = finalHeaders.map(header => {
                const value = rowMap[header] || '';
                return `"${String(value).replace(/"/g, '""')}"`;
            });

            newCsvRows.push(newRow.join(','));
        });
        
        newCsvRows.unshift(finalHeaders.map(h => `"${h}"`).join(','));

        downloadCSV(newCsvRows.join('\n'), 'processed_data.csv');
        document.getElementById('forceSaveButton').style.display = 'none';
    }

    // --- PHASE 2: TEMPLATE FILL LOGIC ---
    async function handleTemplateFill() {
        const cleanedFile = cleanedDataFileInput.files[0];
        const templateFile = templateFileInput.files[0];
        if (!cleanedFile || !templateFile) {
            alert("Please upload both the cleaned data file and the template file.");
            return;
        }

        try {
            const source = await readFileForTemplate(cleanedFile);
            const template = await readFileForTemplate(templateFile);
            
            const outputData = [template.headers.map(h => `"${h}"`).join(',')]; 
            const sourceHeaderIndexMap = new Map(source.headers.map((h, i) => [h, i]));

            source.data.forEach(sourceRow => {
                const newRow = [];
                template.headers.forEach(templateHeader => {
                    const sourceIndex = sourceHeaderIndexMap.get(templateHeader);
                    const value = (sourceIndex !== undefined && sourceRow[sourceIndex]) ? sourceRow[sourceIndex] : '';
                    newRow.push(`"${String(value || '').replace(/"/g, '""')}"`);
                });
                outputData.push(newRow.join(','));
            });

            downloadCSV(outputData.join('\n'), 'final_template_filled.csv');

        } catch (err) {
            alert("Error processing template files: " + err.message);
        }
    }

    function readFileForTemplate(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            const isXLSX = file.name.endsWith('.xlsx');
            
            reader.onload = e => {
                try {
                    let headers, data;
                    if (isXLSX) {
                        const wb = XLSX.read(e.target.result, { type: 'binary' });
                        const ws = wb.Sheets[wb.SheetNames[0]];
                        const rawData = XLSX.utils.sheet_to_json(ws, { header: 1 });
                        headers = rawData[0] ? rawData[0].map(String) : [];
                        data = rawData.length > 1 ? rawData.slice(1) : [];
                    } else {
                        const parsed = parseCSV(e.target.result);
                        headers = parsed.headers;
                        data = parsed.data;
                    }
                    resolve({ headers, data });
                } catch (err) {
                    reject(err);
                }
            };
            reader.onerror = () => reject(new Error("Failed to read file."));

            if (isXLSX) {
                reader.readAsBinaryString(file);
            } else {
                reader.readAsText(file);
            }
        });
    }

    // --- HELPER FUNCTIONS ---
    function isMobile(number) {
        const cleaned = String(number || '').replace(/\D/g, '');
        
        // 1. Standard 04... (10 digits)
        if (/^04\d{8}$/.test(cleaned)) return true;
        
        // 2. Raw 4... (9 digits) <-- ADDED THIS CHECK
        if (/^4\d{8}$/.test(cleaned)) return true;

        // 3. International/Data formats
        if (cleaned.length === 11 && cleaned.startsWith('614')) return true;
        if (cleaned.length === 11 && cleaned.startsWith('64')) return true; 
        
        return false;
    }

    function isLandline(number) {
        const cleaned = String(number || '').replace(/\D/g, '');

        // C# Rules:
        // 1. (^0|^61)[2378]\d{8} -> Starts with 0 or 61 followed by [2378] + 8 digits
        // 2. ^[2-9]\d{7} -> 8 digits starting 2-9
        // 3. ^13\d{4} -> 13xxxx (6 digits)
        // 4. ^1[38]00\d{6} -> 1300/1800 (10 digits)

        if (/^(0|61)[2378]\d{8}$/.test(cleaned)) return true; 
        if (/^[2-9]\d{7}$/.test(cleaned)) return true;        
        if (/^13\d{4}$/.test(cleaned)) return true;           
        if (/^1[38]00\d{6}$/.test(cleaned)) return true;      

        return false;
    }

    function isValidEmail(email) {
        const re = /^(([^<>()[\]\\.,;:\s@"]+(\.[^<>()[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,}))$/;
        return re.test(String(email || '').toLowerCase());
    }

    function cleanAndFormatPhoneNumber(number) {
        let cleanedNumber = String(number || '').replace(/\D/g, '');

        if (/^4\d{8}$/.test(cleanedNumber)) {
            // Raw Mobile: 4XXXXXXXX -> 04XX XXX XXX
            let res = "0" + cleanedNumber;
            return `${res.substring(0, 4)} ${res.substring(4, 7)} ${res.substring(7)}`;
        } 
        else if (/^0614\d{8}$/.test(cleanedNumber)) {
            // 0614... -> 614...
            return cleanedNumber.replace(/^0614/, '614');
        } 
        else if (/^04\d{8}$/.test(cleanedNumber)) {
            // Standard Mobile: 04XXXXXXXX -> 04XX XXX XXX
            return `${cleanedNumber.substring(0, 4)} ${cleanedNumber.substring(4, 7)} ${cleanedNumber.substring(7)}`;
        } 
        else if (/^[2378]\d{8}$/.test(cleanedNumber)) {
            // Local 8-digit landline starting with 2,3,7,8 -> 02 XXXXXXXX
            let res = "0" + cleanedNumber;
            return `${res.substring(0, 2)} ${res.substring(2)}`;
        } 
        else if (/^0061[2378]\d{8}$/.test(cleanedNumber)) {
            // 0061 -> 61...
            let res = cleanedNumber.replace(/^0061/, '61');
            return `${res.substring(0, 3)} ${res.substring(3)}`;
        } 
        else if (/^0[2378]\d{8}$/.test(cleanedNumber)) {
            // Standard Landline: 02XXXXXXXX -> 02 XXXXXXXX
            return `${cleanedNumber.substring(0, 2)} ${cleanedNumber.substring(2)}`;
        } 
        else if (/^61[2378]\d{8}$/.test(cleanedNumber)) {
            // International: 612XXXXXXXX -> 61 2XXXXXXX
            return `${cleanedNumber.substring(0, 3)} ${cleanedNumber.substring(3)}`;
        } 
        else if (/^1[38]00\d{6}$/.test(cleanedNumber)) {
            // 1300/1800 -> 1300 XXXXXX
            return `${cleanedNumber.substring(0, 4)} ${cleanedNumber.substring(4)}`;
        } 
        else if (/^[2-9]\d{7}$/.test(cleanedNumber)) {
            // Local 8-digit generic: 2XXXXXXX -> 2 XXXXXXX
            return `${cleanedNumber.substring(0, 1)} ${cleanedNumber.substring(1)}`;
        } 
        else if (/^0[2-9]\d{7}$/.test(cleanedNumber)) {
            // Local 8-digit with leading 0: 02XXXXXXX -> 2 XXXXXXX (strip 0)
            let res = cleanedNumber.substring(1); 
            return `${res.substring(0, 1)} ${res.substring(1)}`;
        }
        else if (/^13\d{4}$/.test(cleanedNumber)) {
             // 13 Numbers: 13XXXX -> 13 XXXX
             return `${cleanedNumber.substring(0, 2)} ${cleanedNumber.substring(2)}`;
        }

        return cleanedNumber;
    }

    function isValidAustralianPhone(number) {
        const cleaned = String(number || '').replace(/\D/g, '');
        return isMobile(cleaned) || isLandline(cleaned);
    }

    function generateUniqueId() {
        const dateStr = new Date().toISOString().slice(0, 10).replace(/-/g, '');
        const randomStr = Math.random().toString(36).substring(2, 8);
        return `${dateStr}-${randomStr}`;
    }

    function downloadCSV(content, fileName) {
        const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement("a");
        const url = URL.createObjectURL(blob);
        link.setAttribute("href", url);
        link.setAttribute("download", fileName);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    function cleanEmailInput(input) {
        if (!input) return '';
        let cleaned = String(input);
        
        // 1. Remove "mailto:" (case insensitive)
        cleaned = cleaned.replace(/mailto:/gi, '');
        
        // 2. Remove angle brackets < and >
        cleaned = cleaned.replace(/[<>]/g, '');
        
        // 3. Remove ALL spaces (fixes "daryl @domain" -> "daryl@domain")
        // Note: This is safe because we split multiple emails by comma/semicolon later.
        cleaned = cleaned.replace(/\s/g, '');
        
        return cleaned;
    }