from os import path
import pandas as pd
import re

from config.settings.variables import settings

class Dataframe_Analytics:
    """
    Class to handle specific CSV processing tasks for routines 455CGK and 441.
    """
    def processing_csv_455ABH(self, file_path: str):
        """
        Processes the CSV file for routine 455ABH by preserving the metadata on the first line, 
        renaming columns, and removing unwanted ones.
        
        :param file_path: Full path to the CSV file.
        :return: The path to the processed file.
        """
        # 1. Read only the first line (Title/Metadata) to preserve it

        with open(file_path, 'r',encoding='windows-1252') as f:
            primeira_linha = f.readline()

        # 2. Read the DataFrame ignoring the first line (header=1 sets line 2 as header)
        df = pd.read_csv(file_path, sep=';', header=1, dtype=str, encoding='windows-1252')

        # Rename the desired column
        df.rename(
        columns={
            'Base Calculo ICMS/ISS': 'Base de Calculo',
            'Complemento da Ultima Ocorrencia': 'Descricao da Ultima Ocorrencia'
        },
        inplace=True
        )
        # Remove unwanted columns ('Unnamed' columns)
        df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
        
        # 3. Save the file restoring the original structure
        # Step A: Write the first line to the new file
        with open(file_path, 'w', encoding='windows-1252') as f:
            f.write(primeira_linha)
            # Ensures it has a line break in case the original doesn't
            if not primeira_linha.endswith('\n'):
                f.write('\n')
                
        # Step B: Append the DataFrame right below (mode='a' is append)
        df.to_csv(file_path, mode='a', sep=';', index=False, encoding='windows-1252')
        
        return file_path
        
    def processing_csv_455CGK(self, file_path: str):
        """
        Processes the CSV file for routine 455CGK by retaining only the required columns 
        specified in the Pydantic settings.
        
        :param file_path: Full path to the CSV file.
        :return: The path to the processed file.
        """

        # Load the CSV file
        df = pd.read_csv(file_path, sep=';', header=1, encoding='windows-1252', dtype=str, index_col=False, engine='python')

        # Save the file with only the specified columns
        columns_455cgk = settings.yaml_config.variables.bot_455CGK.columns_455CGK
        df.to_csv(file_path, columns=columns_455cgk, sep=';', encoding='windows-1252', index=False)
        
        return file_path
    
    def processing_csv_103(self, file_path: str):
        """
        Processes the CSV file for routine 103 by retaining only the required columns 
        specified in the Pydantic settings.
        
        :param file_path: Full path to the CSV file.
        :return: The path to the processed file.
        """

        # Load the CSV file
        df = pd.read_csv(file_path, sep=';', encoding='windows-1252', dtype=str, index_col=False, engine='python')
        
        # Drop columns out of range (commented out in original)
        # df = df.drop(columns=[col for col in COLUMNS_REMOVE_103 if col in df.columns])
        # for col in COLUMNS_ADD_103:
        #     if col not in df.columns:
        #         df[col] = ''

        # Save the file with only the specified columns
        columns_103 = settings.yaml_config.variables.bot_103.columns_103
        df.to_csv(file_path, columns=columns_103, sep=';', encoding='windows-1252', index=False)

        return file_path
    
    def processing_csv_441(self, file_path: str):
        """
        Processes the CSV file for routine 441, adjusting the structure and keeping 
        only the columns specified in the Pydantic settings.
        
        :param file_path: Full path to the CSV file.
        :return: The path to the processed file.
        """

        # Load the CSV file
        df = pd.read_csv(file_path, sep=';', header=2, encoding='windows-1252', dtype=str, index_col=False, engine='python')
        
        # Rename the column if necessary
        if 'Unnamed: 53' in df.columns:
            df = df.rename(columns={'Unnamed: 53': 'Unnamed: 52'})
        
        # Ensures all expected columns are in the DataFrame
        columns_441 = settings.yaml_config.variables.bot_441.columns_441
        for col in columns_441:
            if col not in df.columns:
                df[col] = ""  # or use `np.nan` if you prefer

        # Reorder to ensure correct order
        df = df[columns_441]

        # Save the CSV with the corrected structure
        df.to_csv(file_path, columns=columns_441, sep=';', encoding='windows-1252', index=False)
        
        return file_path

    def processing_sswweb_324(self, file_path: str):
        """
        Processes the SSWWEB file for routine 324, extracting vehicle data, 
        expenses, and incomes, and exports the result to an Excel file.
        
        :param file_path: Full path to the SSWWEB file.
        :return: The path to the generated Excel file.
        """

        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()

        # Split the file by pages using the "ssw0410" tag
        pages = []
        current_page = []
        for line in lines:
            if "ssw0410" in line:
                if current_page:
                    pages.append(current_page)
                    current_page = []
            current_page.append(line)
        if current_page:
            pages.append(current_page)

        # Process the pages and extract data
        dados_veiculos = []

        for page in pages:
            placa = None

            # Extract plate
            for line in page:
                placa_match = re.search(r"PLACA:\s+([A-Z0-9]+)", line)
                if placa_match:
                    placa = placa_match.group(1)
                    break

            # Extract rows from the table with document, supplier, history, income and expenses
            for line in page:
                match = re.match(
                    r"(\d{2}/\d{2}/\d{2})\s+([\w\-]+)\s+(\d+)\s+(.+?)\s{2,}(.+?)\s{2,}([\d.,]+)\s+([\d.,]+)", line)
                if match:
                    dados_veiculos.append({
                        "Placa": placa,
                        "Emissão": match.group(1),
                        "Documento": f"{match.group(2)} {match.group(3)}",
                        "Fornecedor": match.group(4).strip(),
                        "Histórico": match.group(5).strip(),
                        "Receitas (R$)": float(match.group(6).replace('.', '').replace(',', '.')),
                        "Despesas (R$)": float(match.group(7).replace('.', '').replace(',', '.')),
                    })
        
        df = pd.DataFrame(dados_veiculos)
        file_path = file_path.replace('.sswweb', '.xlsx')
        df.to_excel(file_path, index=False)            

        return file_path
    
    def processing_sswweb_056(self, **kwargs):
        """
        Processes the SSWWEB file for routine 056, adjusting the structure based 
        on the specific report type (020 or 023), and exports to Excel.
        
        :param kwargs: Keyword arguments containing 'file_name' (str) and 'report' (str).
        :return: The path to the generated Excel file.
        """
        file_name = kwargs.get('file_name')
        report = kwargs.get('report')
        
        if report == '020':
            with open(file_name, 'r', encoding='utf-8') as file:
                lines = file.readlines()

            # Splitting file into pages based on keyword "ssw0136"
            pages = []
            current_page = []
            for line in lines:
                if "ssw0136" in line:
                    if current_page:
                        pages.append(current_page)
                        current_page = []
                current_page.append(line)
            if current_page:
                pages.append(current_page)

            # Initialize the final list for the data
            all_data = []

            # Process each page
            for page in pages:
                page_info = {
                    "Motorista": None,
                    "Emissão": None,
                    "CTRB/OS": None,
                    "Custo CTRB-OS/Transf (R$)": None,
                    "Fim da Viagem": None,
                }

                for line in page:
                    # Extracting driver and emission information
                    vehicle_match = re.search(r".+? AGREG .+? MOTORISTA.......: (.+?)\s+EMISSAO....: (.+)", line)
                    if vehicle_match:
                        page_info["Motorista"] = vehicle_match.group(1).strip()
                        page_info["Emissão"] = vehicle_match.group(2).strip()

                    # Extracting CTRB/OS information and costs
                    ctrb_match = re.search(r"CTRB/OS:(.+?)\s+CUSTO CTRB-OS/TRANSF\(A\):\s+([\d.,]+).+FIM VIAGEM.: (.+)", line)
                    if ctrb_match:
                        page_info["CTRB/OS"] = ctrb_match.group(1).strip()
                        page_info["Custo CTRB-OS/Transf (R$)"] = float(ctrb_match.group(2).replace('.', '').replace(',', '.'))
                        page_info["Fim da Viagem"] = ctrb_match.group(3).strip()

                    # Extracting lines from the manifest and directly adding page data
                    manifest_match = re.search(
                        r"(\w+-\w+-\s+\d+)\s+(\w+)\s+(\d+)\s+(\d+)\s+([\d.,]+)\s+([\d.,]+)"
                        r"\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", line)
                    if manifest_match:
                        all_data.append({
                            "Manifesto": manifest_match.group(1).strip(),
                            "Carreta": manifest_match.group(2).strip(),
                            "QCTRC": int(manifest_match.group(3).strip()),
                            "QVOLU": int(manifest_match.group(4).strip()),
                            "KgREAL": float(manifest_match.group(5).replace('.', '').replace(',', '.')),
                            "KgCALC": float(manifest_match.group(6).replace('.', '').replace(',', '.')),
                            "VALMERCAD": float(manifest_match.group(7).replace('.', '').replace(',', '.')),
                            "FRETE": float(manifest_match.group(8).replace('.', '').replace(',', '.')),
                            "FRTPROP": float(manifest_match.group(9).replace('.', '').replace(',', '.')),
                            "FRT/KgC": float(manifest_match.group(10).replace('.', '').replace(',', '.')),
                            "FRT/CTRC": float(manifest_match.group(11).replace('.', '').replace(',', '.')),
                            **page_info  # Directly includes page data in each line of the manifest
                        })

            # Create final DataFrame with "Driver" and "Emission"
            final_df = pd.DataFrame(all_data)
    
            file_name = file_name.replace('.sswweb', '.xlsx')
            final_df.to_excel(file_name, index=False)
            
            return file_name

        elif report == '023':

            with open(file_name, 'r', encoding='utf-8') as file:
                lines = file.readlines()

            # Sort by pages based on marker ssw0702
            pages = []
            current_page = []
            for line in lines:
                if "ssw0702" in line:
                    if current_page:
                        pages.append(current_page)
                        current_page = []
                current_page.append(line)
            if current_page:
                pages.append(current_page)

            # List of extracted data
            all_data = []

            # Process each page
            for page in pages:
                motorista = None
                unidade = None
                data_dia = None
                placa = None
                total_remuneracao = None

                for line in page:
                    # Plate and Driver
                    match_placa_motorista = re.match(r"\s*([A-Z]{3}[0-9A-Z]{4}).*MOTORISTA:\s*(.+)", line)
                    if match_placa_motorista:
                        placa = match_placa_motorista.group(1)
                        motorista = match_placa_motorista.group(2).strip()

                    # Unit and Date
                    match_unidade_data = re.match(r"\s*UNIDADE:\s*([A-Z]{3})-.*?(\d{2}/\d{2}/\d{2})", line)
                    if match_unidade_data:
                        unidade = match_unidade_data.group(1).strip()
                        data_dia = match_unidade_data.group(2)

                    # Total Remuneration
                    match_remun = re.search(r"TOTAL REMUNERACAO \(A\) \(R\$\):\s+([\d.,]+)", line)
                    if match_remun:
                        total_remuneracao = float(match_remun.group(1).replace('.', '').replace(',', '.'))

                # Collect COLLECTION and DELIVERY
                for line in page:
                    if line.strip().startswith("COLETA") or line.strip().startswith("ENTREGA"):
                        tipo = "COLETA" if line.strip().startswith("COLETA") else "ENTREGA"
                        match = re.match(
                            r"\s*(COLETA|ENTREGA)\s+(\d+)?\s+(\d+)?\s+(\d+)?\s+([\d.]+)?\s+([\d.]+)?\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)",
                            line)
                        if match:
                            all_data.append({
                                "Unidade": unidade,
                                "Data": data_dia,
                                "Placa": placa,
                                "Motorista": motorista,
                                "Tipo": tipo,
                                "QCLIE": match.group(2),
                                "QCTRC": match.group(3),
                                "QVOLU": match.group(4),
                                "KgREAL": match.group(5),
                                "KgCALC": match.group(6),
                                "VALMERCAD": float(match.group(7).replace('.', '').replace(',', '.')),
                                "FRT_ROM(B)": float(match.group(8).replace('.', '').replace(',', '.')),
                                "FRT_ENT(C)": float(match.group(9).replace('.', '').replace(',', '.')),
                                "TOTAL REMUNERACAO (A) (R$)": total_remuneracao
                            })
                    
            # Create final DataFrame with "Driver" and "Emission"
            final_df = pd.DataFrame(all_data)

            file_name = file_name.replace('.sswweb', '.xlsx')
            final_df.to_excel(file_name, index=False)
                    
            return file_name
        
        elif report == '087':
            # ---------------------------------------------------------
            # REPORT 087 - SSW 3201
            # ---------------------------------------------------------

            REPORT_MARKER = "ssw3201"
            END_MARKER = "RAIO DO LOCAL DO LOCAL DE ENTREGA:"

            DATE_RE = re.compile(
                r"DATA EMISSAO ROMANEIO:\s*(\d{2}/\d{2}/\d{2})",
                re.IGNORECASE
            )

            UNIT_RE = re.compile(
                r"UNIDADE:\s*([A-Z0-9]+)\s*-",
                re.IGNORECASE
            )

            # Exemplos:
            # ALF113589-9
            # LNO000165-1
            CTRC_RE = re.compile(
                r"^[A-Z0-9]{3}\d{6}-\d$"
            )

            # ---------------------------------------------------------
            # Leitura do arquivo
            # ---------------------------------------------------------
            with open(file_name, 'rb') as file:
                raw_data = file.read()

            # Arquivos SSW podem variar de encoding.
            text = None

            for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
                try:
                    text = raw_data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if text is None:
                text = raw_data.decode('utf-8', errors='replace')

            # ---------------------------------------------------------
            # Funções auxiliares do parser
            # ---------------------------------------------------------
            def is_column_ruler(line):
                """
                Identifica a régua que separa as colunas.

                Exemplo:
                ------------+----------------+----------+---------
                """
                stripped = line.strip()

                return (
                    "+" in stripped
                    and "-" in stripped
                    and set(stripped) <= {"-", "+"}
                )

            def ranges_from_ruler(ruler):
                """
                Usa a posição dos sinais '+' para descobrir
                onde cada coluna começa e termina.
                """
                plus_positions = [
                    index
                    for index, char in enumerate(ruler)
                    if char == "+"
                ]

                starts = [0] + [
                    position + 1
                    for position in plus_positions
                ]

                ends = plus_positions + [len(ruler)]

                return list(zip(starts, ends))

            def slice_line(line, column_ranges):
                """
                Divide uma linha usando as posições encontradas
                na régua de colunas.
                """
                return [
                    line[start:end].strip()
                    for start, end in column_ranges
                ]

            # ---------------------------------------------------------
            # Processamento do relatório
            # ---------------------------------------------------------
            all_data = []

            in_report = False

            dt_roman = ""
            unidade = ""

            column_ranges = []
            headers = []

            for raw_line in text.splitlines():

                # Remove caracteres de controle que podem aparecer
                # no início de páginas do SSW.
                line = (
                    raw_line
                    .replace("\x13", "")
                    .replace("\x0c", "")
                )

                # -----------------------------------------------------
                # Início de uma página do relatório 3201
                # -----------------------------------------------------
                if REPORT_MARKER.lower() in line.lower():
                    in_report = True
                    continue

                if not in_report:
                    continue

                # -----------------------------------------------------
                # Fim da parte detalhada
                #
                # Tudo abaixo deste marcador é resumo e não deve
                # entrar no resultado.
                # -----------------------------------------------------
                if END_MARKER.lower() in line.lower():
                    break

                # -----------------------------------------------------
                # DATA EMISSÃO ROMANEIO
                # -----------------------------------------------------
                date_match = DATE_RE.search(line)

                if date_match:
                    dt_roman = date_match.group(1)
                    continue

                # -----------------------------------------------------
                # UNIDADE
                # -----------------------------------------------------
                unit_match = UNIT_RE.search(line)

                if unit_match:
                    unidade = unit_match.group(1).upper()
                    continue

                # -----------------------------------------------------
                # Régua das colunas
                # -----------------------------------------------------
                if is_column_ruler(line):
                    column_ranges = ranges_from_ruler(line)
                    continue

                # -----------------------------------------------------
                # Cabeçalho
                # -----------------------------------------------------
                if (
                    column_ranges
                    and line.lstrip().startswith("CTRC")
                    and "PAGADOR/ABC" in line
                ):
                    headers = slice_line(
                        line,
                        column_ranges
                    )

                    continue

                # Ainda não encontramos a estrutura da tabela.
                if not column_ranges or not headers:
                    continue

                # -----------------------------------------------------
                # Dados da tabela
                # -----------------------------------------------------
                values = slice_line(
                    line,
                    column_ranges
                )

                if not values:
                    continue

                if not values[0]:
                    continue

                # -----------------------------------------------------
                # Somente CTRCs válidos
                #
                # Ignora:
                # - TOTAL
                # - ROMANEIO
                # - títulos
                # - linhas vazias
                # - resumos
                # -----------------------------------------------------
                if not CTRC_RE.fullmatch(values[0]):
                    continue

                row = {
                    "DT ROMAN": dt_roman,
                    "UNIDADE": unidade,
                }

                row.update(
                    dict(zip(headers, values))
                )

                all_data.append(row)

            # ---------------------------------------------------------
            # Validações
            # ---------------------------------------------------------
            if not headers:
                raise ValueError(
                    "Cabeçalho da tabela SSW 3201 "
                    "não foi encontrado no report 087."
                )

            if not all_data:
                raise ValueError(
                    "Nenhum CTRC foi encontrado "
                    "no report 087 antes do marcador de fim."
                )

            # ---------------------------------------------------------
            # DataFrame
            # ---------------------------------------------------------
            final_df = pd.DataFrame(all_data)

            # ---------------------------------------------------------
            # Geração do CSV
            # Mesmo padrão dos outros CSVs do projeto:
            # separador ';' e encoding 'windows-1252'.
            # ---------------------------------------------------------
            output_file = file_name.replace(
                '.sswweb',
                '.csv'
            )

            final_df.to_csv(
                output_file,
                sep=';',
                encoding='windows-1252',
                errors='replace',
                index=False
            )

            return output_file

    def processing_sswweb_119(self, **kwargs):
        """
        Converts a formatted TXT/SSWWEB file for routine 119 into a structured Excel file.

        :param kwargs: Keyword arguments containing 'file_name' (str).
        :return: The path to the generated Excel file.
        """
        file_name = kwargs.get('file_name')
        # List to store extracted data
        extracted_data = []

        # Read the TXT file
        with open(file_name, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            
        # Control variable for the current salesperson
        current_salesperson = ""

        # Define field lengths based on the "+" character
        fields = {
            "CNPJ": (0, 14),
            "CLIENTE": (14, 43),
            "DIA": (43, 52),
            "TIPO": (52, 63),
            "ACOES_VENDA": (63, 79),
            "RELATORIO": (79, None)  # None indicates until the end of the line
        }

        for line in lines:
            # Detect the salesperson
            if "VENDEDOR:" in line:
                current_salesperson = line.split(":")[1].split("CODIGO")[0].strip()

            # Extract data from the main lines
            if len(line) > 80 and line[0].isdigit():
                cnpj = line[fields["CNPJ"][0]:fields["CNPJ"][1]].strip()
                client = line[fields["CLIENTE"][0]:fields["CLIENTE"][1]].strip()
                day = line[fields["DIA"][0]:fields["DIA"][1]].strip()
                type_ = line[fields["TIPO"][0]:fields["TIPO"][1]].strip()
                sales_actions = line[fields["ACOES_VENDA"][0]:fields["ACOES_VENDA"][1]].strip()
                report = line[fields["RELATORIO"][0]:].strip()

                # Add the data to the list
                extracted_data.append({
                    "VENDEDOR": current_salesperson,
                    "CNPJ": cnpj,
                    "CLIENTE": client,
                    "DIA": day,
                    "TIPO": type_,
                    "ACOES_VENDA": sales_actions,
                    "RELATORIO": report
                })

        # Create a DataFrame
        dataframe = pd.DataFrame(extracted_data)
        new_file_path = file_name.replace(".sswweb", ".xlsx")
        # Export to Excel
        dataframe.to_excel(new_file_path, index=False)
        
        return new_file_path
    
    def processing_csv_452(self, file_name: str):
        """
        Processes the CSV file for routine 452: drops the SSW title lines above
        the header (header on line 3) and rewrites the file with all original
        columns — including pandas' 'Unnamed: N' for the trailing ';' — in
        windows-1252.

        :param file_name: Full path to the CSV file (rewritten in place).
        """
        # Load the CSV file
        df = pd.read_csv(file_name, sep=';',  header=2, encoding='windows-1252', dtype=str, index_col=False, engine='python')

        # Save the file with its current structure but standardized encoding
        df.to_csv(file_name, sep=';', encoding='windows-1252', index=False)