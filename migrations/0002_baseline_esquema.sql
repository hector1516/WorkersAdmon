-- BASELINE: esquema actual de ECCSA_Admon_Pruebas (generado, SOLO referencia)
-- Los cambios futuros se agregan como migraciones numeradas; no editar este archivo.
-- Tabla: [Automoviles]
CREATE TABLE dbo.[Automoviles] (
    [id] numeric(18,0) NOT NULL,
    [Automovil] varchar(50) NULL
);

-- Tabla: [Autos]
CREATE TABLE dbo.[Autos] (
    [Modelo] varchar(100) NULL,
    [Año] int NULL,
    [Marca] varchar(100) NULL
);

-- Tabla: [Clientes]
CREATE TABLE dbo.[Clientes] (
    [IdCliente] varchar(4) NOT NULL,
    [Cliente] varchar(MAX) NOT NULL,
    [CondicionesPagoDias] int NOT NULL
);

-- Tabla: [ClientesBak]
CREATE TABLE dbo.[ClientesBak] (
    [IdCliente] varchar(4) NOT NULL,
    [Cliente] varchar(MAX) NOT NULL,
    [CondicionesPagoDias] int NOT NULL
);

-- Tabla: [ControlAsistencia]
CREATE TABLE dbo.[ControlAsistencia] (
    [id] varchar(14) NULL,
    [Nombre] varchar(50) NULL,
    [Semana] int NULL,
    [Año] int NULL,
    [fecha] date NULL,
    [HoraE] varchar(12) NULL,
    [HoraS] varchar(12) NULL,
    [THoras] varchar(50) NULL,
    [RestHrs] varchar(50) NULL,
    [Observaciones] varchar(MAX) NULL
);

-- Tabla: [ControlAsistenciaUsrs]
CREATE TABLE dbo.[ControlAsistenciaUsrs] (
    [Indice] int NOT NULL,
    [Nombre] varchar(50) NOT NULL
);

-- Tabla: [Correos]
CREATE TABLE dbo.[Correos] (
    [EnviarA] varchar(50) NULL,
    [De] varchar(50) NULL,
    [Asunto] varchar(50) NULL,
    [Mensaje] varchar(MAX) NULL,
    [ArchivoCot] varchar(MAX) NULL,
    [FolioCM] numeric(18,0) NULL,
    [FechaHoraEnvio] datetime NULL,
    [idCliente] varchar(4) NULL
);

-- Tabla: [CotizacionesIndiceExcel]
CREATE TABLE dbo.[CotizacionesIndiceExcel] (
    [Id_Registro] int NOT NULL IDENTITY,
    [Fecha] date NULL,
    [Cliente] varchar(100) NULL,
    [Folio] int NULL,
    [Nombre_Contacto] varchar(100) NULL,
    [Descripcion] text NULL,
    [Elaboro] varchar(50) NULL,
    [Realizo] varchar(50) NULL,
    [Orden_Compra] varchar(100) NULL,
    [Factura] varchar(100) NULL
);

-- Tabla: [CotizacionesReportes]
CREATE TABLE dbo.[CotizacionesReportes] (
    [Folio] varchar(20) NOT NULL,
    [Cliente] varchar(200) NOT NULL,
    [Contacto] varchar(200) NULL,
    [Fecha] date NOT NULL,
    [Descripcion] varchar(MAX) NULL,
    [Subtotal] decimal(18,2) NOT NULL,
    [Notas] varchar(MAX) NULL,
    [Estatus] varchar(20) NOT NULL
);

-- Tabla: [CotizacionesServDetalle]
CREATE TABLE dbo.[CotizacionesServDetalle] (
    [Id] int NOT NULL IDENTITY,
    [FolioCotizacion] int NOT NULL,
    [Partida] int NOT NULL,
    [Cantidad] decimal(18,2) NOT NULL,
    [Descripcion] nvarchar(MAX) NOT NULL,
    [Modelo] nvarchar(200) NOT NULL,
    [PCompraUni] decimal(18,2) NOT NULL,
    [PrecioCompraTotal] decimal(18,2) NOT NULL,
    [Factor] decimal(18,4) NOT NULL,
    [PVentaUnitario] decimal(18,2) NOT NULL,
    [PVentaTotal] decimal(18,2) NOT NULL,
    [Ganancia] decimal(18,2) NOT NULL,
    [Proveedor] nvarchar(MAX) NOT NULL,
    [TiempoEntrega] nvarchar(200) NOT NULL
);

-- Tabla: [CotizacionesServDetalleUni]
CREATE TABLE dbo.[CotizacionesServDetalleUni] (
    [Id] int NOT NULL IDENTITY,
    [FolioCotizacion] int NOT NULL,
    [Descripcion] nvarchar(MAX) NOT NULL
);

-- Tabla: [CotMatProv]
CREATE TABLE dbo.[CotMatProv] (
    [Indice] numeric(18,0) NULL,
    [Nombre] varchar(50) NULL,
    [Archivo] varbinary(MAX) NULL
);

-- Tabla: [DolarV]
CREATE TABLE dbo.[DolarV] (
    [Dolar] decimal(18,5) NULL,
    [Fecha] date NULL
);

-- Tabla: [ErrorLog]
CREATE TABLE dbo.[ErrorLog] (
    [FechaHora] datetime NULL,
    [Titulo] varchar(50) NULL,
    [Descripcion] varchar(MAX) NULL
);

-- Tabla: [Guardia]
CREATE TABLE dbo.[Guardia] (
    [id] varchar(14) NOT NULL,
    [Persona] varchar(50) NULL,
    [FechaIni] date NULL,
    [FechaFin] date NULL
);

-- Tabla: [HorasE]
CREATE TABLE dbo.[HorasE] (
    [id] numeric(18,0) NOT NULL,
    [Nombre] varchar(50) NOT NULL,
    [Estado] int NOT NULL,
    [Fecha] datetime NOT NULL,
    [Horas] decimal(18,0) NOT NULL,
    [Acumulado] decimal(18,0) NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [Entrada] time NOT NULL,
    [Salida] time NOT NULL,
    [Comida] decimal(18,0) NULL,
    [ViajeIda] decimal(18,0) NULL,
    [ViajeReg] decimal(18,0) NULL
);

-- Tabla: [HUB_ActivityLog]
CREATE TABLE dbo.[HUB_ActivityLog] (
    [Id] int NOT NULL IDENTITY,
    [FechaHora] datetime NOT NULL,
    [Usuario] varchar(250) NOT NULL,
    [Modulo] varchar(100) NOT NULL,
    [Accion] varchar(MAX) NOT NULL
);

-- Tabla: [HUB_AIConfig]
CREATE TABLE dbo.[HUB_AIConfig] (
    [Id] int NOT NULL,
    [Provider] varchar(50) NOT NULL,
    [ApiKey] varchar(500) NOT NULL,
    [Model] varchar(100) NOT NULL,
    [UpdatedAt] datetime NOT NULL
);

-- Tabla: [HUB_Automoviles]
CREATE TABLE dbo.[HUB_Automoviles] (
    [Id] int NOT NULL IDENTITY,
    [MarcaModelo] varchar(150) NOT NULL,
    [Placas] varchar(30) NOT NULL,
    [PolizaSeguro] varchar(150) NULL,
    [IdUsuarioAsignado] int NULL,
    [UltimoServicioKms] int NOT NULL
);

-- Tabla: [HUB_BingWallpapers]
CREATE TABLE dbo.[HUB_BingWallpapers] (
    [Id] int NOT NULL IDENTITY,
    [Url] varchar(500) NOT NULL,
    [FechaRegistro] datetime NULL
);

-- Tabla: [HUB_EmailConfig]
CREATE TABLE dbo.[HUB_EmailConfig] (
    [Id] int NOT NULL IDENTITY,
    [SmtpServer] varchar(255) NOT NULL,
    [Port] int NOT NULL,
    [Username] varchar(255) NOT NULL,
    [Password] varchar(255) NOT NULL,
    [UseSSL] bit NOT NULL,
    [UseTLS] bit NOT NULL,
    [RequireAuth] bit NOT NULL
);

-- Tabla: [HUB_Games]
CREATE TABLE dbo.[HUB_Games] (
    [Id] int NOT NULL IDENTITY,
    [Username] nvarchar(200) NOT NULL,
    [Score] int NOT NULL,
    [Date] date NOT NULL
);

-- Tabla: [HUB_GmailTokens]
CREATE TABLE dbo.[HUB_GmailTokens] (
    [IdUsuario] int NOT NULL,
    [GmailAccount] varchar(255) NOT NULL,
    [RefreshToken] varchar(MAX) NOT NULL,
    [AccessToken] varchar(MAX) NULL,
    [ExpiresAt] datetime NULL,
    [FechaRegistro] datetime NULL,
    [LastSyncTime] datetime NULL
);

-- Tabla: [HUB_HorasExtrasRegistros]
CREATE TABLE dbo.[HUB_HorasExtrasRegistros] (
    [Id] int NOT NULL IDENTITY,
    [IdUsuario] int NOT NULL,
    [Fecha] date NOT NULL,
    [HoraEntrada] time NULL,
    [HoraSalida] time NULL,
    [HorasComida] decimal(4,2) NULL,
    [HorasTraslado] decimal(4,2) NULL,
    [HorasExtrasCalculadas] decimal(5,2) NOT NULL,
    [Descripcion] nvarchar(1000) NOT NULL,
    [Cliente] nvarchar(300) NULL,
    [Calificacion] int NULL,
    [Estatus] varchar(20) NOT NULL,
    [FechaRegistro] datetime NOT NULL
);

-- Tabla: [HUB_JarvisConversaciones]
CREATE TABLE dbo.[HUB_JarvisConversaciones] (
    [Id] int NOT NULL IDENTITY,
    [IdUsuario] int NOT NULL,
    [Titulo] nvarchar(300) NOT NULL,
    [FechaCreacion] datetime NULL
);

-- Tabla: [HUB_JarvisMensajes]
CREATE TABLE dbo.[HUB_JarvisMensajes] (
    [Id] int NOT NULL IDENTITY,
    [IdConversacion] int NOT NULL,
    [Role] nvarchar(40) NOT NULL,
    [Contenido] nvarchar(MAX) NOT NULL,
    [FechaRegistro] datetime NULL
);

-- Tabla: [HUB_Notificaciones]
CREATE TABLE dbo.[HUB_Notificaciones] (
    [Id] int NOT NULL IDENTITY,
    [Titulo] nvarchar(510) NULL,
    [Mensaje] nvarchar(MAX) NULL,
    [Autor] nvarchar(510) NULL,
    [FechaEnvio] datetime NULL,
    [EnviadoA] int NULL,
    [RecibidoPor] int NULL
);

-- Tabla: [HUB_OxxoGasTickets]
CREATE TABLE dbo.[HUB_OxxoGasTickets] (
    [Id] int NOT NULL IDENTITY,
    [FolioTicket] varchar(100) NOT NULL,
    [ImagenTicket] varbinary(MAX) NULL,
    [ImagenNombre] varchar(255) NULL,
    [IdVehiculo] int NULL,
    [IdCliente] varchar(50) NULL,
    [Descripcion] varchar(500) NULL,
    [IdUsuario] int NOT NULL,
    [FechaRegistro] datetime NULL
);

-- Tabla: [HUB_OxxoGasVales]
CREATE TABLE dbo.[HUB_OxxoGasVales] (
    [Id] int NOT NULL IDENTITY,
    [MessageId] varchar(255) NOT NULL,
    [Fecha] datetime NOT NULL,
    [Remitente] varchar(255) NULL,
    [Asunto] varchar(255) NULL,
    [Monto] decimal(18,2) NULL,
    [PdfName] varchar(255) NULL,
    [PdfContent] varbinary(MAX) NULL,
    [XmlName] varchar(255) NULL,
    [XmlContent] varbinary(MAX) NULL,
    [FolioTicket] varchar(100) NULL,
    [IdVehiculo] int NULL,
    [IdCliente] varchar(50) NULL,
    [IdUsuario] int NULL,
    [Descripcion] varchar(500) NULL,
    [FechaRegistro] datetime NULL,
    [XmlFolio] varchar(100) NULL,
    [XmlEstacion] varchar(255) NULL,
    [XmlLitros] decimal(10,4) NULL,
    [XmlConcepto] varchar(255) NULL
);

-- Tabla: [HUB_PushConfig]
CREATE TABLE dbo.[HUB_PushConfig] (
    [Id] int NOT NULL,
    [VapidPublicKey] nvarchar(512) NULL,
    [VapidPrivateKey] nvarchar(512) NULL,
    [VapidEmail] nvarchar(512) NULL,
    [UpdatedAt] datetime NULL
);

-- Tabla: [HUB_PushSubscriptions]
CREATE TABLE dbo.[HUB_PushSubscriptions] (
    [Id] int NOT NULL IDENTITY,
    [UserEmail] nvarchar(510) NULL,
    [Endpoint] nvarchar(2048) NULL,
    [P256dhKey] nvarchar(512) NULL,
    [AuthKey] nvarchar(512) NULL,
    [CreatedAt] datetime NULL
);

-- Tabla: [HUB_RegistroKilometros]
CREATE TABLE dbo.[HUB_RegistroKilometros] (
    [Id] int NOT NULL IDENTITY,
    [IdAutomovil] int NOT NULL,
    [Kilometros] int NOT NULL,
    [FechaHora] datetime NOT NULL,
    [IdUsuario] int NOT NULL
);

-- Tabla: [HUB_ReparacionesAutomovil]
CREATE TABLE dbo.[HUB_ReparacionesAutomovil] (
    [Id] int NOT NULL IDENTITY,
    [IdAutomovil] int NOT NULL,
    [Fecha] date NOT NULL,
    [Costo] decimal(18,2) NOT NULL,
    [Taller] varchar(250) NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL
);

-- Tabla: [HUB_ServiciosAutomovil]
CREATE TABLE dbo.[HUB_ServiciosAutomovil] (
    [Id] int NOT NULL IDENTITY,
    [IdAutomovil] int NOT NULL,
    [Fecha] date NOT NULL,
    [Kilometros] int NOT NULL,
    [Notas] varchar(MAX) NULL
);

-- Tabla: [HUB_Sessions]
CREATE TABLE dbo.[HUB_Sessions] (
    [Token] varchar(64) NOT NULL,
    [UserEmail] varchar(255) NOT NULL,
    [CreatedAt] datetime NOT NULL,
    [ExpiresAt] datetime NOT NULL
);

-- Tabla: [HUB_SignatureTokens]
CREATE TABLE dbo.[HUB_SignatureTokens] (
    [Token] varchar(64) NOT NULL,
    [IdReporte] int NOT NULL,
    [CreatedAt] datetime NOT NULL,
    [ExpiresAt] datetime NOT NULL,
    [UsedAt] datetime NULL
);

-- Tabla: [HUB_Users]
CREATE TABLE dbo.[HUB_Users] (
    [Id] int NOT NULL IDENTITY,
    [Email] varchar(150) NOT NULL,
    [Nombre] varchar(100) NOT NULL,
    [Password] varchar(100) NOT NULL,
    [Activo] bit NULL,
    [FechaCreado] datetime NULL,
    [AccesoCotizaciones] bit NOT NULL,
    [AccesoVM] bit NOT NULL,
    [AccesoConfiguracion] bit NOT NULL,
    [AccesoUsuarios] bit NOT NULL,
    [AccesoReportes] bit NOT NULL,
    [AccesoRegistroReportes] bit NOT NULL,
    [AccesoCotizacionesReportes] bit NULL,
    [AccesoClientes] int NOT NULL,
    [AccesoRegistroKilometros] int NOT NULL,
    [AccesoAutomoviles] int NOT NULL,
    [AccesoConfigurarCorreo] bit NOT NULL,
    [AccesoConfigAI] bit NOT NULL,
    [Notificaciones] bit NULL,
    [FechaIngreso] date NULL,
    [AccesoVacaciones] bit NOT NULL,
    [AccesoMisVacaciones] bit NULL,
    [AccesoHorasExtras] bit NOT NULL,
    [AccesoMisHorasExtras] bit NOT NULL,
    [AccesoOxxoGas] tinyint NOT NULL,
    [AccesoValesOxxoGas] tinyint NOT NULL,
    [AccesoRegistroTicketOxxoGas] tinyint NOT NULL
);

-- Tabla: [HUB_VacacionesRegistros]
CREATE TABLE dbo.[HUB_VacacionesRegistros] (
    [Id] int NOT NULL IDENTITY,
    [IdUsuario] int NOT NULL,
    [Fecha] date NOT NULL,
    [Tipo] varchar(50) NOT NULL,
    [Notas] nvarchar(510) NULL,
    [FechaRegistro] datetime NOT NULL
);

-- Tabla: [HUB_VacacionesSaldosManuales]
CREATE TABLE dbo.[HUB_VacacionesSaldosManuales] (
    [IdUsuario] int NOT NULL,
    [DiasAcumuladosAnteriores] int NOT NULL,
    [PeriodoAcumulado] varchar(50) NULL
);

-- Tabla: [IAContexto]
CREATE TABLE dbo.[IAContexto] (
    [Contexto] varchar(MAX) NULL
);

-- Tabla: [ImagenesPersonal]
CREATE TABLE dbo.[ImagenesPersonal] (
    [Nombre] varchar(50) NOT NULL,
    [Imagen] image NULL
);

-- Tabla: [ImagenesPersonal_]
CREATE TABLE dbo.[ImagenesPersonal_] (
    [Nombre] varchar(50) NOT NULL,
    [Imagen] varbinary(MAX) NULL
);

-- Tabla: [IMSSCorr]
CREATE TABLE dbo.[IMSSCorr] (
    [Correo] varchar(MAX) NOT NULL,
    [Cliente] varchar(MAX) NULL
);

-- Tabla: [IndiceCot]
CREATE TABLE dbo.[IndiceCot] (
    [Folio] numeric(18,0) NOT NULL IDENTITY,
    [IdCliente] varchar(4) NOT NULL,
    [Contacto] varchar(MAX) NOT NULL,
    [Fecha] date NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [Nota] varchar(MAX) NULL,
    [Autor] varchar(50) NOT NULL
);

-- Tabla: [IndiceCotServ]
CREATE TABLE dbo.[IndiceCotServ] (
    [Folio] numeric(18,0) NOT NULL IDENTITY,
    [IdCliente] varchar(4) NOT NULL,
    [Contacto] varchar(MAX) NOT NULL,
    [Fecha] date NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [Nota] varchar(MAX) NULL,
    [Autor] varchar(50) NOT NULL,
    [Color] int NULL
);

-- Tabla: [IndiceMateriales]
CREATE TABLE dbo.[IndiceMateriales] (
    [Folio] numeric(18,0) NOT NULL IDENTITY,
    [IdCliente] varchar(4) NOT NULL,
    [Contacto] varchar(MAX) NOT NULL,
    [Fecha] date NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [Nota] varchar(MAX) NULL,
    [Autor] varchar(50) NOT NULL,
    [Color] int NULL
);

-- Tabla: [IndicePedidos]
CREATE TABLE dbo.[IndicePedidos] (
    [Folio] numeric(18,0) NOT NULL IDENTITY,
    [Proveedor] varchar(MAX) NOT NULL,
    [Contacto] varchar(MAX) NULL,
    [Fecha] date NULL,
    [Descripcion] varchar(MAX) NULL,
    [Nota] varchar(MAX) NULL,
    [Autor] varchar(50) NULL,
    [FechaRecibido] date NULL,
    [Recibido] varchar(50) NULL,
    [FechaEnviado] date NULL,
    [Enviado] varchar(50) NULL,
    [IdCliente] varchar(50) NULL,
    [Color] int NULL
);

-- Tabla: [IndiceReportes]
CREATE TABLE dbo.[IndiceReportes] (
    [Folio] nvarchar(24) NOT NULL,
    [Fecha] date NULL,
    [Autor] nvarchar(100) NULL,
    [Cliente] nvarchar(MAX) NULL,
    [Contacto] nvarchar(100) NULL,
    [Maquina] nvarchar(100) NULL,
    [Actividades] nvarchar(MAX) NULL,
    [FechaEntrada] datetime NULL,
    [FechaSalida] datetime NULL,
    [Comida] float NULL,
    [Translado] float NULL,
    [Cotizacion] nvarchar(100) NULL,
    [Firma] varbinary(MAX) NULL
);

-- Tabla: [IndiceReportesFirma]
CREATE TABLE dbo.[IndiceReportesFirma] (
    [Folio] nchar(24) NOT NULL,
    [Firma] varbinary(MAX) NULL
);

-- Tabla: [IndiceReportesFisicos]
CREATE TABLE dbo.[IndiceReportesFisicos] (
    [Folio] nchar(24) NOT NULL,
    [Reporte] varbinary(MAX) NULL
);

-- Tabla: [IndiceReportesSu]
CREATE TABLE dbo.[IndiceReportesSu] (
    [Folio] varchar(12) NOT NULL,
    [Fecha] date NULL,
    [Cliente] varchar(50) NULL,
    [Cotizacion] varchar(50) NULL,
    [Autor] varchar(50) NULL
);

-- Tabla: [IndicesExcel]
CREATE TABLE dbo.[IndicesExcel] (
    [Anio] int NOT NULL,
    [Fecha] date NULL,
    [CodigoCliente] nvarchar(100) NULL,
    [ID_Registro] int NOT NULL,
    [Responsable] nvarchar(510) NULL,
    [DescripcionServicio] nvarchar(MAX) NULL,
    [TipoOC] nvarchar(100) NULL,
    [Iniciales] nvarchar(20) NULL,
    [FolioInterno] nvarchar(200) NULL,
    [ReferenciaD] nvarchar(200) NULL,
    [NotasAdicionales] nvarchar(MAX) NULL,
    [FechaInsercion] datetime NULL
);

-- Tabla: [Inventario]
CREATE TABLE dbo.[Inventario] (
    [id] varchar(20) NULL,
    [Pieza] varchar(MAX) NULL,
    [Modelo] varchar(MAX) NULL,
    [Marca] varchar(MAX) NULL,
    [Descripción] varchar(MAX) NULL,
    [UbicaciónA] varchar(MAX) NULL,
    [UbicacionB] varchar(MAX) NULL,
    [Observaciones] varchar(MAX) NULL
);

-- Tabla: [Inventario_]
CREATE TABLE dbo.[Inventario_] (
    [id] varchar(14) NULL,
    [Pieza] varchar(MAX) NULL,
    [Modelo] varchar(MAX) NULL,
    [Marca] varchar(MAX) NULL,
    [Descripcion] varchar(MAX) NULL,
    [UbicacionA] varchar(MAX) NULL,
    [UbicacionB] varchar(MAX) NULL,
    [Observaciones] varchar(MAX) NULL,
    [Imagen] image NULL
);

-- Tabla: [InventarioMat]
CREATE TABLE dbo.[InventarioMat] (
    [Id_Inventario] int NOT NULL IDENTITY,
    [SerieInterno] nvarchar(200) NOT NULL,
    [FechaRegistro] datetime NULL,
    [Marca] nvarchar(200) NULL,
    [Modelo] nvarchar(200) NULL,
    [Descripcion] nvarchar(MAX) NULL,
    [Vendidos] int NULL,
    [StockAlmacen] int NULL,
    [PedidosRealizados] int NULL,
    [CantAPedir] int NULL,
    [Minimo] int NULL,
    [Maximo] int NULL,
    [PrecioUSD] decimal(18,2) NULL,
    [PrecioMXN] decimal(18,2) NULL,
    [SAT_CODE] nvarchar(100) NULL,
    [Impuestos] decimal(18,2) NULL,
    [Notas] nvarchar(MAX) NULL,
    [Proveedor] nvarchar(400) NULL,
    [TiempoEntrega] nvarchar(200) NULL,
    [Autor] nvarchar(200) NULL
);

-- Tabla: [ListaCambios]
CREATE TABLE dbo.[ListaCambios] (
    [FechaHora] datetime NULL,
    [Version] varchar(10) NULL,
    [Cambios] varchar(MAX) NULL
);

-- Tabla: [MAC]
CREATE TABLE dbo.[MAC] (
    [MAC] char(12) NOT NULL,
    [Nombre] varchar(50) NULL,
    [Equipo] varchar(50) NULL,
    [Admin] bit NULL,
    [Online] bit NULL,
    [Telefono] varchar(50) NULL,
    [Cotizaciones] bit NULL,
    [Pedidos] bit NULL,
    [Kms] bit NULL,
    [Guardia] bit NULL,
    [Calculos] bit NULL,
    [Reportes] bit NULL,
    [Imagen] varbinary(MAX) NULL
);

-- Tabla: [MACLogins]
CREATE TABLE dbo.[MACLogins] (
    [FechaHora] datetime NULL,
    [MAC] varchar(50) NULL,
    [Equipo] varchar(50) NULL
);

-- Tabla: [MaqVirtual]
CREATE TABLE dbo.[MaqVirtual] (
    [id] varchar(50) NULL,
    [Nombre] varchar(100) NULL,
    [Descripcion] varchar(MAX) NULL,
    [SistemaOperativo] varchar(50) NULL
);

-- Tabla: [MaqVirtualSoft]
CREATE TABLE dbo.[MaqVirtualSoft] (
    [id] varchar(MAX) NULL,
    [Nombre] varchar(MAX) NULL,
    [Version] varchar(MAX) NULL,
    [MaqVirtual] varchar(MAX) NULL,
    [Descripcion] varchar(MAX) NULL
);

-- Tabla: [Partidas]
CREATE TABLE dbo.[Partidas] (
    [Folio] numeric(18,0) NOT NULL,
    [Partida] int NOT NULL,
    [Cantidad] int NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [PrecioCompraUnitario] decimal(18,2) NOT NULL,
    [Factor] decimal(18,2) NOT NULL,
    [Proveedor] varchar(MAX) NOT NULL,
    [TiempoEntregaDias] int NOT NULL,
    [Dolar] decimal(18,2) NULL,
    [Flete] decimal(18,2) NULL
);

-- Tabla: [Partidas_]
CREATE TABLE dbo.[Partidas_] (
    [Folio] numeric(18,0) NOT NULL,
    [Partida] int NOT NULL,
    [Cantidad] int NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [PrecioCompraUnitario] decimal(18,2) NOT NULL,
    [PrecioCompraTotal] decimal(29,2) NULL,
    [Factor] decimal(18,2) NOT NULL,
    [PrecioVentaUnitario] decimal(38,4) NULL,
    [PrecioVentaTotal] decimal(38,4) NULL,
    [FactorTot] decimal(38,4) NULL,
    [Proveedor] varchar(MAX) NOT NULL,
    [TiempoEntregaDias] int NOT NULL,
    [Dolar] decimal(18,2) NULL,
    [Flete] decimal(18,2) NULL
);

-- Tabla: [PartidasPedidos]
CREATE TABLE dbo.[PartidasPedidos] (
    [Folio] numeric(18,0) NOT NULL,
    [Partida] int NOT NULL,
    [Cantidad] int NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [PrecioUnitario] decimal(18,2) NOT NULL,
    [Dolar] bit NULL
);

-- Tabla: [PartidasProv]
CREATE TABLE dbo.[PartidasProv] (
    [Folio] numeric(18,0) NOT NULL,
    [Partida] int NOT NULL,
    [Cantidad] int NOT NULL,
    [Descripcion] varchar(MAX) NOT NULL,
    [PrecioCompraUnitario] decimal(18,2) NOT NULL,
    [TiempoEntregaDias] int NOT NULL,
    [Dolar] bit NULL,
    [Cotizacion] varchar(MAX) NULL
);

-- Tabla: [Prov]
CREATE TABLE dbo.[Prov] (
    [Prov] varchar(MAX) NOT NULL,
    [CondicionesPagoDias] int NOT NULL
);

-- Tabla: [Recordatorios]
CREATE TABLE dbo.[Recordatorios] (
    [Folio] varchar(14) NULL,
    [EnviarA] varchar(MAX) NULL,
    [Asunto] varchar(50) NULL,
    [Mensaje] varchar(MAX) NULL,
    [Adjunto] varchar(MAX) NULL,
    [Fecha] date NULL,
    [IntervaloDias] int NULL,
    [CorreoNotifica] varchar(MAX) NULL
);

-- Tabla: [RegistroKMs]
CREATE TABLE dbo.[RegistroKMs] (
    [id] numeric(18,0) NOT NULL,
    [Fecha] date NULL,
    [Automovil] varchar(50) NULL,
    [Kilometros] numeric(18,0) NULL
);

-- Tabla: [RegistroKms_]
CREATE TABLE dbo.[RegistroKms_] (
    [id] varchar(16) NOT NULL,
    [FechaRegistro] date NULL,
    [FechaLunes] date NULL,
    [Automovil] varchar(50) NULL,
    [Kilometros] varchar(50) NULL,
    [Autor] varchar(50) NULL
);

-- Tabla: [ReportesServicio]
CREATE TABLE dbo.[ReportesServicio] (
    [IdReporte] int NOT NULL IDENTITY,
    [Folio] varchar(20) NOT NULL,
    [Cliente] varchar(200) NOT NULL,
    [Contacto] varchar(200) NULL,
    [Fecha] date NOT NULL,
    [Tecnico] varchar(150) NOT NULL,
    [DescripcionServicio] varchar(MAX) NULL,
    [Estatus] varchar(50) NOT NULL,
    [Notas] varchar(MAX) NULL,
    [FechaHoraInicio] datetime NULL,
    [FechaHoraFin] datetime NULL,
    [TiempoTraslado] decimal(5,2) NULL,
    [TiempoComida] bit NULL,
    [CorreoContacto] varchar(150) NULL,
    [FirmaConformidad] varchar(MAX) NULL,
    [Cotizacion] varchar(50) NULL
);

-- Tabla: [schema_migrations]
CREATE TABLE dbo.[schema_migrations] (
    [id] int NOT NULL IDENTITY,
    [version] nvarchar(510) NOT NULL,
    [created_at] datetime2 NOT NULL,
    [applied_by] nvarchar(510) NULL
);

-- Tabla: [UsuariosVales]
CREATE TABLE dbo.[UsuariosVales] (
    [id] numeric(18,0) NOT NULL,
    [Usuario] varchar(50) NULL
);

-- Tabla: [Vales]
CREATE TABLE dbo.[Vales] (
    [id] numeric(18,0) NOT NULL,
    [Fecha] date NULL,
    [Nombre] varchar(50) NULL,
    [Automovil] varchar(50) NULL,
    [CantidadVale] decimal(18,0) NULL,
    [Empresa] varchar(50) NULL,
    [Proyecto] varchar(50) NULL
);
